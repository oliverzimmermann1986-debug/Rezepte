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
const CLOUDFLARE_CLIENT_ID_KEY = 'cloudflare-client-id';
const CLOUDFLARE_CLIENT_SECRET_KEY = 'cloudflare-client-secret';
const USERNAME_KEY = 'rezepte.username';
const INSTALL_MARKER = FileSystem.documentDirectory
  ? `${FileSystem.documentDirectory}.rezepte-install-v1`
  : null;
const DEFAULT_SERVER = String(Constants.expoConfig?.extra?.apiUrl || '').replace(/\/+$/, '');
const KEYCHAIN_SERVICE = String(
  Constants.expoConfig?.extra?.keychainService || 'de.mausbaeren.rezepte',
);
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainService: KEYCHAIN_SERVICE,
};
const AUTH_KEYS = [
  TOKEN_KEY,
  SERVER_KEY,
  CLOUDFLARE_CLIENT_ID_KEY,
  CLOUDFLARE_CLIENT_SECRET_KEY,
  USERNAME_KEY,
] as const;
const SESSION_KEYS = [TOKEN_KEY, USERNAME_KEY] as const;

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
  if (!__DEV__ && DEFAULT_SERVER) {
    const expected = new URL(DEFAULT_SERVER);
    if (parsed.origin !== expected.origin) {
      throw new ApiError('Diese App verbindet sich nur mit dem fest hinterlegten Rezeptserver.', 0);
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
  cloudflareClientId: string;
  cloudflareClientSecret: string;
  sessionWarning: string;
  sessionChecking: boolean;
  authCleanupPending: boolean;
  signIn: (
    serverUrl: string,
    username: string,
    password: string,
    nextCloudflareClientId: string,
    nextCloudflareClientSecret: string,
  ) => Promise<void>;
  signOut: () => Promise<void>;
  refreshSession: () => Promise<void>;
  retryAuthCleanup: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState(DEFAULT_SERVER);
  const [username, setUsername] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [cloudflareClientId, setCloudflareClientId] = useState('');
  const [cloudflareClientSecret, setCloudflareClientSecret] = useState('');
  const [sessionWarning, setSessionWarning] = useState('');
  const [sessionChecking, setSessionChecking] = useState(false);
  const [authCleanupPending, setAuthCleanupPending] = useState(false);
  const sessionRefreshInFlight = useRef<Promise<void> | null>(null);

  useEffect(() => {
    prepareSecureStorage()
      .then(() => Promise.all([
        secureStorage.get(TOKEN_KEY),
        secureStorage.get(SERVER_KEY),
        secureStorage.get(CLOUDFLARE_CLIENT_ID_KEY),
        secureStorage.get(CLOUDFLARE_CLIENT_SECRET_KEY),
        secureStorage.get(USERNAME_KEY),
      ]))
      .then(async ([storedToken, storedServer, storedClientId, storedClientSecret, storedUsername]) => {
        const server = storedServer || DEFAULT_SERVER;
        const cloudflare = storedClientId && storedClientSecret
          ? { clientId: storedClientId, clientSecret: storedClientSecret }
          : null;
        configureApi(server, storedToken, cloudflare);
        setToken(storedToken);
        setServerUrl(server);
        setCloudflareClientId(storedClientId || '');
        setCloudflareClientSecret(storedClientSecret || '');
        setUsername(storedUsername || '');
        if (storedToken && server) {
          try {
            const session = await api<{
              username: string;
              role?: string;
              is_admin?: boolean;
            }>('/api/auth/session');
            setUsername(session.username);
            setIsAdmin(session.is_admin === true || session.role === 'admin');
            await secureStorage.set(USERNAME_KEY, session.username);
          } catch (reason) {
            if (reason instanceof ApiError && reason.status === 401) {
              try {
                await purgeStoredSessionWithRetryMarker();
                setAuthCleanupPending(false);
              } catch {
                setAuthCleanupPending(true);
              }
              configureApi(server, null, cloudflare);
              setToken(null);
              setUsername('');
              setIsAdmin(false);
              setSessionWarning('Deine Sitzung ist abgelaufen. Der Gerätezugang bleibt gespeichert.');
            } else if (reason instanceof ApiError && reason.status === 403) {
              // Cloudflare oder eine fehlende Backend-Berechtigung darf keine
              // gültigen Gerätezugangsdaten aus dem Schlüsselbund löschen.
              setSessionWarning('Der Server hat den Zugriff abgelehnt. Gerätezugang und Sitzung bleiben gespeichert.');
            } else {
              // Kein Netz/5xx ist keine Abmeldung. Gecachte Rezepte bleiben
              // lesbar und die Session wird beim nächsten Request erneut geprüft.
              setSessionWarning('Server gerade nicht erreichbar – gespeicherte Inhalte werden angezeigt.');
            }
          }
        }
      })
      .catch(() => {
        configureApi('', null, null);
        setToken(null);
        setUsername('');
        setIsAdmin(false);
        setServerUrl(DEFAULT_SERVER);
        setCloudflareClientId('');
        setCloudflareClientSecret('');
        setAuthCleanupPending(true);
        setSessionWarning('Der iOS-Schlüsselbund konnte nicht vollständig bereinigt werden. Bitte erneut versuchen.');
      })
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    setUnauthorizedHandler(async requestEpoch => {
      if (!isApiSessionEpochCurrent(requestEpoch)) return;
      const cloudflare = cloudflareClientId && cloudflareClientSecret
        ? { clientId: cloudflareClientId, clientSecret: cloudflareClientSecret }
        : null;
      // Zuerst synchron die laufende Sitzung entwerten. Langsame oder
      // fehlschlagende Schlüsselbund-/Cache-Operationen dürfen die UI nicht
      // in einem halb angemeldeten Zustand lassen.
      configureApi(serverUrl, null, cloudflare);
      setToken(null);
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
      await clearApiCache().catch(() => undefined);
    });
    return () => setUnauthorizedHandler(null);
  }, [cloudflareClientId, cloudflareClientSecret, ready, serverUrl]);

  const refreshSession = useCallback(async () => {
    if (!ready || !token || !serverUrl) return;
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
          const cloudflare = cloudflareClientId && cloudflareClientSecret
            ? { clientId: cloudflareClientId, clientSecret: cloudflareClientSecret }
            : null;
          configureApi(serverUrl, null, cloudflare);
          setToken(null);
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
          await clearApiCache().catch(() => undefined);
        } else if (reason instanceof ApiError && reason.status === 403) {
          // Weder Rolle noch Cloudflare-Zugang bei einer vorübergehenden
          // Ablehnung verwerfen. Ein späterer Fokus/Retry prüft erneut.
          setSessionWarning('Der Server hat den Zugriff abgelehnt. Gerätezugang und Sitzung bleiben gespeichert.');
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
    cloudflareClientId,
    cloudflareClientSecret,
    ready,
    serverUrl,
    token,
  ]);

  useEffect(() => {
    if (!ready || !token) return;
    const subscription = AppState.addEventListener('change', state => {
      if (state === 'active' && sessionWarning) void refreshSession();
    });
    return () => subscription.remove();
  }, [ready, refreshSession, sessionWarning, token]);

  async function signIn(
    nextServer: string,
    nextUsername: string,
    password: string,
    nextCloudflareClientId: string,
    nextCloudflareClientSecret: string,
  ) {
    const normalizedServer = normalizeServer(nextServer);
    const normalizedClientId = nextCloudflareClientId.trim();
    const normalizedClientSecret = nextCloudflareClientSecret.trim();
    if (Boolean(normalizedClientId) !== Boolean(normalizedClientSecret)) {
      throw new ApiError('Cloudflare Client-ID und Client-Secret müssen gemeinsam eingetragen werden.', 0);
    }
    const cloudflare = normalizedClientId && normalizedClientSecret
      ? { clientId: normalizedClientId, clientSecret: normalizedClientSecret }
      : null;
    configureApi(normalizedServer, null, cloudflare);
    const result = await api<{
      token: string;
      username: string;
      role?: string;
      is_admin?: boolean;
    }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: nextUsername.trim(), password }),
    });
    try {
      await clearApiCache();
      await secureStorage.set(SERVER_KEY, normalizedServer);
      if (normalizedClientId) await secureStorage.set(CLOUDFLARE_CLIENT_ID_KEY, normalizedClientId);
      else await secureStorage.delete(CLOUDFLARE_CLIENT_ID_KEY);
      if (normalizedClientSecret) await secureStorage.set(CLOUDFLARE_CLIENT_SECRET_KEY, normalizedClientSecret);
      else await secureStorage.delete(CLOUDFLARE_CLIENT_SECRET_KEY);
      await secureStorage.set(TOKEN_KEY, result.token);
      await secureStorage.set(USERNAME_KEY, result.username);
    } catch (reason) {
      const cleanup = await Promise.allSettled([removeInstallMarker(), purgeStoredAuth()]);
      if (cleanup.some(resultState => resultState.status === 'rejected')) {
        setAuthCleanupPending(true);
      }
      configureApi('', null, null);
      throw reason;
    }
    configureApi(normalizedServer, result.token, cloudflare);
    setServerUrl(normalizedServer);
    setCloudflareClientId(normalizedClientId);
    setCloudflareClientSecret(normalizedClientSecret);
    setToken(result.token);
    setUsername(result.username);
    setIsAdmin(result.is_admin === true || result.role === 'admin');
    setSessionWarning('');
    setAuthCleanupPending(false);
    router.replace('/(tabs)');
  }

  async function signOut() {
    // Der Request startet noch mit einem Schnappschuss der alten Sitzung. Die
    // UI wird unmittelbar danach lokal abgemeldet; eine verspätete Antwort
    // gehört dank Session-Epoch weiterhin zur alten Sitzung.
    const serverLogout = token
      ? api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
      : Promise.resolve();
    configureApi('', null, null);
    setToken(null);
    setUsername('');
    setIsAdmin(false);
    setServerUrl(DEFAULT_SERVER);
    setCloudflareClientId('');
    setCloudflareClientSecret('');
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
    cloudflareClientId,
    cloudflareClientSecret,
    sessionWarning,
    sessionChecking,
    authCleanupPending,
    signIn,
    signOut,
    refreshSession,
    retryAuthCleanup,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth muss innerhalb von AuthProvider verwendet werden');
  return value;
}
