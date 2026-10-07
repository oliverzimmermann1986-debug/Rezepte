import * as SecureStore from 'expo-secure-store';
import * as FileSystem from 'expo-file-system/legacy';
import Constants from 'expo-constants';
import { Image } from 'expo-image';
import { router } from 'expo-router';
import React, {
  createContext,
  PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import { AppState } from 'react-native';

import {
  ApiError,
  api,
  configureApi,
  currentApiSessionEpoch,
  isApiSessionEpochCurrent,
  setUnauthorizedHandler,
} from './api';
import { clearApiCache } from './cache';

const TOKEN_KEY = 'api-token';
const SERVER_KEY = 'rezepte.server';
// Retired credentials are deletion-only migration keys, never request headers.
const LEGACY_ACCESS_KEYS = ['cloudflare-client-id', 'cloudflare-client-secret'] as const;
const USERNAME_KEY = 'rezepte.username';
const INSTALL_MARKER = FileSystem.documentDirectory
  ? `${FileSystem.documentDirectory}.rezepte-install-v1`
  : null;
const DEFAULT_SERVER = String(Constants.expoConfig?.extra?.apiUrl || '').replace(/\/+$/, '');
const ALLOWED_SERVER_ORIGINS = new Set(
  [
    DEFAULT_SERVER,
    ...(Array.isArray(Constants.expoConfig?.extra?.allowedApiUrls)
      ? Constants.expoConfig.extra.allowedApiUrls
      : []),
  ]
    .map(value => {
      try {
        return new URL(String(value || '').trim()).origin;
      } catch {
        return '';
      }
    })
    .filter(Boolean),
);
const KEYCHAIN_SERVICE = String(
  Constants.expoConfig?.extra?.keychainService || 'de.mausbaeren.rezepte',
);
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainService: KEYCHAIN_SERVICE,
};
const AUTH_KEYS = [
  TOKEN_KEY,
  SERVER_KEY,
  ...LEGACY_ACCESS_KEYS,
  USERNAME_KEY,
] as const;
const SESSION_KEYS = [TOKEN_KEY, USERNAME_KEY, ...LEGACY_ACCESS_KEYS] as const;

const secureStorage = {
  get: (key: string) => SecureStore.getItemAsync(key, KEYCHAIN_OPTIONS),
  set: (key: string, value: string) => SecureStore.setItemAsync(key, value, {
    ...KEYCHAIN_OPTIONS,
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  }),
  delete: (key: string) => SecureStore.deleteItemAsync(key, KEYCHAIN_OPTIONS),
};

async function deleteStoredKeys(keys: readonly string[]) {
  const results = await Promise.allSettled(keys.map(key => secureStorage.delete(key)));
  const failed = results.filter(result => result.status === 'rejected');
  if (failed.length) {
    throw new Error(`${failed.length} Schlüsselbund-Einträge konnten nicht gelöscht werden.`);
  }
}

async function purgeStoredAuth() {
  await deleteStoredKeys(AUTH_KEYS);
}

async function purgeStoredSession() {
  await deleteStoredKeys(SESSION_KEYS);
}

async function clearExpiredSessionCaches() {
  await Promise.allSettled([
    clearApiCache(),
    Image.clearMemoryCache(),
    Image.clearDiskCache(),
  ]);
}

async function purgeStoredSessionWithRetryMarker() {
  try {
    await purgeStoredSession();
  } catch (reason) {
    // Falls die Sitzung im Schlüsselbund verblieben ist, erzwingt ein
    // fehlender Marker beim nächsten Start eine vollständige Bereinigung.
    await removeInstallMarker().catch(() => undefined);
    throw reason;
  }
}

async function removeInstallMarker() {
  if (INSTALL_MARKER) await FileSystem.deleteAsync(INSTALL_MARKER, { idempotent: true });
}

async function writeInstallMarker() {
  if (INSTALL_MARKER) await FileSystem.writeAsStringAsync(INSTALL_MARKER, '1');
}

async function prepareSecureStorage() {
  if (!INSTALL_MARKER) return;
  const marker = await FileSystem.getInfoAsync(INSTALL_MARKER);
  if (marker.exists) return;

  // iOS kann Keychain-Einträge über eine Deinstallation hinweg behalten. App-Daten
  // hingegen werden entfernt; ein fehlender Marker kennzeichnet daher die erste
  // Ausführung dieser Installation und darf keine alte Sitzung wiederverwenden.
  await purgeStoredAuth();
  await writeInstallMarker();
}

function normalizeServer(value: string) {
  const trimmed = value.trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new ApiError('Die Server-Adresse ist ungültig.', 0);
  }
  const localDevelopment = __DEV__
    && parsed.protocol === 'http:'
    && ['localhost', '127.0.0.1'].includes(parsed.hostname);
  if (parsed.protocol !== 'https:' && !localDevelopment) {
    throw new ApiError('Die Server-Adresse muss HTTPS verwenden.', 0);
  }
  if (parsed.username || parsed.password) {
    throw new ApiError('Die Server-Adresse darf keine Zugangsdaten enthalten.', 0);
  }
  if ((parsed.pathname && parsed.pathname !== '/') || parsed.search || parsed.hash) {
    throw new ApiError('Bitte nur die Server-Adresse ohne Pfad, Parameter oder # eingeben.', 0);
  }
  if (!__DEV__ && ALLOWED_SERVER_ORIGINS.size) {
    if (!ALLOWED_SERVER_ORIGINS.has(parsed.origin)) {
      throw new ApiError('Diese App verbindet sich nur mit einem freigegebenen Rezeptserver.', 0);
    }
  }
  return parsed.origin;
}

type AuthContextValue = {
  ready: boolean;
  token: string | null;
  serverUrl: string;
  username: string;
  isAdmin: boolean;
  isGuest: boolean;
  sessionWarning: string;
  sessionChecking: boolean;
  authCleanupPending: boolean;
  signIn: (
    serverUrl: string,
    username: string,
    password: string,
  ) => Promise<void>;
  signInAsGuest: (server: string) => Promise<void>;
  registerAccount: (server: string, username: string, password: string,
                    invitationToken?: string) => Promise<void>;
  signOut: () => Promise<void>;
  returnToLogin: () => Promise<void>;
  refreshSession: () => Promise<void>;
  refreshHousehold: () => Promise<void>;
  retryAuthCleanup: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState(DEFAULT_SERVER);
  const [username, setUsername] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [isGuest, setIsGuest] = useState(false);
  const [sessionWarning, setSessionWarning] = useState('');
  const [sessionChecking, setSessionChecking] = useState(false);
  const [authCleanupPending, setAuthCleanupPending] = useState(false);
  const sessionRefreshInFlight = useRef<Promise<void> | null>(null);
  const authenticationInFlight = useRef(false);

  useEffect(() => {
    prepareSecureStorage()
      .then(async () => {
        try {
          await deleteStoredKeys(LEGACY_ACCESS_KEYS);
        } catch {
          // A failed migration must not discard a valid app session. Retry only
          // these retired entries while the user remains signed in.
          setAuthCleanupPending(true);
          setSessionWarning('Alte Zugangsdaten konnten noch nicht vollständig aus dem Schlüsselbund entfernt werden.');
        }
      })
      .then(() => Promise.all([
        secureStorage.get(TOKEN_KEY),
        secureStorage.get(SERVER_KEY),
        secureStorage.get(USERNAME_KEY),
      ]))
      .then(async ([storedToken, storedServer, storedUsername]) => {
        const server = storedServer || DEFAULT_SERVER;
        // Older builds used a placeholder instead of an app session. It cannot
        // authorize API calls after Access has been retired.
        const legacySession = storedToken?.trim() === 'cloudflare-access';
        const sessionToken = legacySession ? null : storedToken;
        if (legacySession) {
          try {
            await purgeStoredSessionWithRetryMarker();
            setAuthCleanupPending(false);
          } catch {
            setAuthCleanupPending(true);
          }
          await clearExpiredSessionCaches();
          setSessionWarning('Bitte mit deinem Rezeptkonto anmelden.');
        }
        configureApi(server, sessionToken, legacySession ? '' : storedUsername || '');
        setToken(sessionToken);
        setIsGuest(Boolean(sessionToken?.startsWith('guest.')));
        setServerUrl(server);
        setUsername(legacySession ? '' : storedUsername || '');
        if (sessionToken && server) {
          try {
            const session = await api<{
              username: string;
              role?: string;
              is_admin?: boolean;
            }>('/api/auth/session', {}, undefined, 5_000);
            setUsername(session.username);
            setIsAdmin(session.is_admin === true || session.role === 'admin');
            setIsGuest(session.role === 'guest');
            await secureStorage.set(USERNAME_KEY, session.username);
          } catch (reason) {
            if (reason instanceof ApiError && reason.status === 401) {
              try {
                await purgeStoredSessionWithRetryMarker();
                setAuthCleanupPending(false);
              } catch {
                setAuthCleanupPending(true);
              }
              configureApi(server, null);
              setToken(null);
              setIsGuest(false);
              setUsername('');
              setIsAdmin(false);
              setSessionWarning('Deine Sitzung ist abgelaufen. Bitte erneut anmelden.');
              await clearExpiredSessionCaches();
            } else if (reason instanceof ApiError && reason.status === 403) {
              // A permission failure does not invalidate an app session.
              setSessionWarning('Der Server hat den Zugriff abgelehnt. Deine Sitzung bleibt gespeichert.');
            } else {
              // Kein Netz/5xx ist keine Abmeldung. Gecachte Rezepte bleiben
              // lesbar und die Session wird beim nächsten Request erneut geprüft.
              setSessionWarning('Server gerade nicht erreichbar – gespeicherte Inhalte werden angezeigt.');
            }
          }
        }
      })
      .catch(() => {
        configureApi('', null);
        setToken(null);
        setIsGuest(false);
        setUsername('');
        setIsAdmin(false);
        setServerUrl(DEFAULT_SERVER);
        setAuthCleanupPending(true);
        setSessionWarning('Der iOS-Schlüsselbund konnte nicht vollständig bereinigt werden. Bitte erneut versuchen.');
      })
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    setUnauthorizedHandler(async requestEpoch => {
      if (!isApiSessionEpochCurrent(requestEpoch)) return;
      // Zuerst synchron die laufende Sitzung entwerten. Langsame oder
      // fehlschlagende Schlüsselbund-/Cache-Operationen dürfen die UI nicht
      // in einem halb angemeldeten Zustand lassen.
      configureApi(serverUrl, null);
      setToken(null);
      setIsGuest(false);
      setUsername('');
      setIsAdmin(false);
      setSessionWarning('Deine Sitzung ist abgelaufen. Bitte erneut anmelden.');
      router.replace('/login');
      try {
        await purgeStoredSessionWithRetryMarker();
        setAuthCleanupPending(false);
      } catch {
        setAuthCleanupPending(true);
        setSessionWarning('Sitzung abgelaufen. Der Schlüsselbund konnte noch nicht vollständig bereinigt werden.');
      }
      await clearExpiredSessionCaches();
    });
    return () => setUnauthorizedHandler(null);
  }, [ready, serverUrl]);

  const refreshSession = useCallback(async () => {
    if (!ready || !token || !serverUrl || authenticationInFlight.current) return;
    if (sessionRefreshInFlight.current) return sessionRefreshInFlight.current;

    const requestEpoch = currentApiSessionEpoch();
    const operation = (async () => {
      setSessionChecking(true);
      try {
        const session = await api<{
          username: string;
          role?: string;
          is_admin?: boolean;
        }>('/api/auth/session');
        // Während der Prüfung kann sich der Benutzer ab- oder neu anmelden.
        // Eine Antwort der alten Sitzung darf die neue Rolle nicht überschreiben.
        if (!isApiSessionEpochCurrent(requestEpoch)) return;
        setUsername(session.username);
        setIsAdmin(session.is_admin === true || session.role === 'admin');
        setIsGuest(session.role === 'guest');
        try {
          await secureStorage.set(USERNAME_KEY, session.username);
          setSessionWarning('');
        } catch {
          setSessionWarning('Sitzung aktiv. Der Benutzername konnte lokal nicht gespeichert werden.');
        }
      } catch (reason) {
        // Ein 401 kann bereits den globalen Handler ausgelöst haben. Falls er
        // die Session-Epoch geändert hat, ist die Abmeldung vollständig und
        // dieser alte Check darf keine weitere Zustandsänderung auslösen.
        if (!isApiSessionEpochCurrent(requestEpoch)) return;
        if (reason instanceof ApiError && reason.status === 401) {
          configureApi(serverUrl, null);
          setToken(null);
          setIsGuest(false);
          setUsername('');
          setIsAdmin(false);
          setSessionWarning('Deine Sitzung ist abgelaufen. Bitte erneut anmelden.');
          router.replace('/login');
          try {
            await purgeStoredSessionWithRetryMarker();
            setAuthCleanupPending(false);
          } catch {
            setAuthCleanupPending(true);
            setSessionWarning('Sitzung abgelaufen. Der Schlüsselbund konnte noch nicht vollständig bereinigt werden.');
          }
          await clearExpiredSessionCaches();
        } else if (reason instanceof ApiError && reason.status === 403) {
          // Keep the confirmed session and role on a temporary rejection.
          setSessionWarning('Der Server hat den Zugriff abgelehnt. Deine Sitzung bleibt gespeichert.');
        } else {
          // Auch Netzwerk- und 5xx-Fehler lassen die zuletzt bestätigte Rolle
          // unangetastet, damit ein temporärer Ausfall keine Rechte flackern lässt.
          setSessionWarning('Server gerade nicht erreichbar – gespeicherte Inhalte werden angezeigt.');
        }
      } finally {
        setSessionChecking(false);
      }
    })();
    sessionRefreshInFlight.current = operation;
    try {
      await operation;
    } finally {
      if (sessionRefreshInFlight.current === operation) sessionRefreshInFlight.current = null;
    }
  }, [
    ready,
    serverUrl,
    token,
  ]);

  useEffect(() => {
    if (!ready || !token) return;
    const subscription = AppState.addEventListener('change', state => {
      // Rollen können sich serverseitig ändern, ohne dass die bisherige
      // Sitzung fehlschlägt. Deshalb bei jedem Zurückkehren prüfen und nicht
      // nur dann, wenn bereits eine Warnung sichtbar ist.
      if (state === 'active') void refreshSession();
    });
    return () => subscription.remove();
  }, [ready, refreshSession, token]);

  async function signIn(
    nextServer: string,
    nextUsername: string,
    password: string,
    mode: 'login' | 'guest' | 'register' = 'login',
    invitationToken = '',
  ) {
    const normalizedServer = normalizeServer(nextServer);
    if (authenticationInFlight.current) throw new ApiError('Eine Anmeldung läuft bereits.', 0);
    authenticationInFlight.current = true;
    try {
      configureApi(normalizedServer, null);
      const attemptEpoch = currentApiSessionEpoch();
      let result: { token: string; username: string; role?: string; is_admin?: boolean };
      try {
        result = await api<{
          token: string;
          username: string;
          role?: string;
          is_admin?: boolean;
        }>(`/api/auth/${mode}`, {
          method: 'POST',
          body: JSON.stringify(mode === 'guest' ? {} : { username: nextUsername.trim(), password, invitation_token: invitationToken.trim() }),
        });
        if (typeof result?.token !== 'string' || !result.token.trim() || result.token.trim() === 'cloudflare-access') {
          throw new ApiError('Der Server hat keine gültige App-Sitzung geliefert. Bitte später erneut anmelden.', 502);
        }
      } catch (reason) {
        // Eine fehlgeschlagene Registrierung aus dem Gastkonto lässt dessen
        // Lesesitzung aktiv. Ein zwischenzeitliches Abmelden bleibt wirksam.
        if (token && isApiSessionEpochCurrent(attemptEpoch)) {
          configureApi(serverUrl, token, username);
        }
        throw reason;
      }
      try {
        await clearApiCache();
        await secureStorage.set(SERVER_KEY, normalizedServer);
        await secureStorage.set(TOKEN_KEY, result.token);
        await secureStorage.set(USERNAME_KEY, result.username);
        if (!isApiSessionEpochCurrent(attemptEpoch)) throw new ApiError('Die Anmeldung wurde beendet.', 401);
      } catch (reason) {
        const cleanup = await Promise.allSettled([removeInstallMarker(), purgeStoredAuth()]);
        if (cleanup.some(resultState => resultState.status === 'rejected')) {
          setAuthCleanupPending(true);
        }
        configureApi('', null);
        setToken(null);
        setIsGuest(false);
        setUsername('');
        setIsAdmin(false);
        setSessionWarning('Anmeldung konnte auf dem Gerät nicht gespeichert werden. Bitte erneut anmelden.');
        router.replace('/login');
        throw reason;
      }
      let legacyCleanupPending = false;
      try {
        await deleteStoredKeys(LEGACY_ACCESS_KEYS);
      } catch {
        legacyCleanupPending = true;
      }
      if (!isApiSessionEpochCurrent(attemptEpoch)) throw new ApiError('Die Anmeldung wurde beendet.', 401);
      configureApi(normalizedServer, result.token, result.username);
      setServerUrl(normalizedServer);
      setToken(result.token);
      setIsGuest(result.role === 'guest');
      setUsername(result.username);
      setIsAdmin(result.is_admin === true || result.role === 'admin');
      setSessionWarning(legacyCleanupPending ? 'Alte Zugangsdaten konnten noch nicht vollständig aus dem Schlüsselbund entfernt werden.' : '');
      setAuthCleanupPending(legacyCleanupPending);
      router.replace('/(tabs)');
    } finally {
      authenticationInFlight.current = false;
    }
  }

  async function returnToLogin() {
    if (authenticationInFlight.current) return;
    configureApi(serverUrl, null);
    setToken(null);
    setIsGuest(false);
    setUsername('');
    setIsAdmin(false);
    setSessionWarning('');
    router.replace('/login');
    try {
      await purgeStoredSessionWithRetryMarker();
      setAuthCleanupPending(false);
    } catch {
      setAuthCleanupPending(true);
      setSessionWarning('Die Gastsitzung konnte im Schlüsselbund noch nicht vollständig gelöscht werden.');
    }
    await Promise.allSettled([clearApiCache(), Image.clearMemoryCache(), Image.clearDiskCache()]);
  }

  async function signOut() {
    // Der Request startet noch mit einem Schnappschuss der alten Sitzung. Die
    // UI wird unmittelbar danach lokal abgemeldet; eine verspätete Antwort
    // gehört dank Session-Epoch weiterhin zur alten Sitzung.
    const serverLogout = token
      ? api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
      : Promise.resolve();
    configureApi('', null);
    setToken(null);
    setIsGuest(false);
    setUsername('');
    setIsAdmin(false);
    setServerUrl(DEFAULT_SERVER);
    setSessionWarning('');
    router.replace('/login');
    const storageCleanup = (async () => {
      try {
        const [markerResult, purgeResult] = await Promise.allSettled([
          removeInstallMarker(),
          purgeStoredAuth(),
        ]);
        if (markerResult.status === 'rejected' || purgeResult.status === 'rejected') {
          throw new Error('Schlüsselbund-Bereinigung unvollständig');
        }
        await writeInstallMarker();
        setAuthCleanupPending(false);
      } catch {
        // Der fehlende Installationsmarker erzwingt beim nächsten Start einen
        // erneuten Löschversuch, bevor alte Zugangsdaten gelesen werden.
        setAuthCleanupPending(true);
        setSessionWarning('Abgemeldet. Einige Schlüsselbund-Daten konnten noch nicht gelöscht werden.');
      }
    })();
    await Promise.allSettled([
      serverLogout,
      storageCleanup,
      clearApiCache(),
      Image.clearMemoryCache(),
      Image.clearDiskCache(),
    ]);
  }

  async function retryAuthCleanup() {
    try {
      if (token) {
        await deleteStoredKeys(LEGACY_ACCESS_KEYS);
        setAuthCleanupPending(false);
        setSessionWarning('');
        return;
      }
      const cleanup = await Promise.allSettled([removeInstallMarker(), purgeStoredAuth()]);
      if (cleanup.some(resultState => resultState.status === 'rejected')) {
        throw new Error('Schlüsselbund-Bereinigung unvollständig');
      }
      await writeInstallMarker();
      setAuthCleanupPending(false);
      setSessionWarning('');
    } catch {
      setAuthCleanupPending(true);
      setSessionWarning('Der Schlüsselbund konnte weiterhin nicht vollständig bereinigt werden.');
    }
  }

  const value = {
    ready,
    token,
    serverUrl,
    username,
    isAdmin,
    isGuest,
    sessionWarning,
    sessionChecking,
    authCleanupPending,
    signIn,
    signInAsGuest: (server: string) => signIn(server, '', '', 'guest'),
    registerAccount: (server: string, name: string, password: string, invitationToken = '') =>
      signIn(server, name, password, 'register', invitationToken),
    signOut,
    returnToLogin,
    refreshSession,
    refreshHousehold: async () => {
      // A membership change invalidates in-flight reads and all stored content
      // before the newly shared household can be displayed.
      configureApi(serverUrl, token, username);
      await clearApiCache();
      await Image.clearDiskCache();
      await Image.clearMemoryCache();
      await refreshSession();
    },
    retryAuthCleanup,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth muss innerhalb von AuthProvider verwendet werden');
  return value;
}
