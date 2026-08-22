import * as SecureStore from 'expo-secure-store';
import * as FileSystem from 'expo-file-system/legacy';
import Constants from 'expo-constants';
import { Image } from 'expo-image';
import { router } from 'expo-router';
import React, { createContext, PropsWithChildren, useContext, useEffect, useState } from 'react';

import { ApiError, api, configureApi, setUnauthorizedHandler } from './api';
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

const secureStorage = {
  get: (key: string) => SecureStore.getItemAsync(key, KEYCHAIN_OPTIONS),
  set: (key: string, value: string) => SecureStore.setItemAsync(key, value, {
    ...KEYCHAIN_OPTIONS,
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  }),
  delete: (key: string) => SecureStore.deleteItemAsync(key, KEYCHAIN_OPTIONS),
};

async function purgeStoredAuth() {
  await Promise.allSettled(AUTH_KEYS.map(key => secureStorage.delete(key)));
}

async function prepareSecureStorage() {
  if (!INSTALL_MARKER) return;
  const marker = await FileSystem.getInfoAsync(INSTALL_MARKER);
  if (marker.exists) return;

  // iOS kann Keychain-Einträge über eine Deinstallation hinweg behalten. App-Daten
  // hingegen werden entfernt; ein fehlender Marker kennzeichnet daher die erste
  // Ausführung dieser Installation und darf keine alte Sitzung wiederverwenden.
  await purgeStoredAuth();
  await FileSystem.writeAsStringAsync(INSTALL_MARKER, '1');
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
  cloudflareClientId: string;
  cloudflareClientSecret: string;
  sessionWarning: string;
  signIn: (
    serverUrl: string,
    username: string,
    password: string,
    nextCloudflareClientId: string,
    nextCloudflareClientSecret: string,
  ) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState(DEFAULT_SERVER);
  const [username, setUsername] = useState('');
  const [cloudflareClientId, setCloudflareClientId] = useState('');
  const [cloudflareClientSecret, setCloudflareClientSecret] = useState('');
  const [sessionWarning, setSessionWarning] = useState('');

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
            const session = await api<{ username: string }>('/api/auth/session');
            setUsername(session.username);
            await secureStorage.set(USERNAME_KEY, session.username);
          } catch (reason) {
            if (reason instanceof ApiError && [401, 403].includes(reason.status)) {
              await purgeStoredAuth();
              configureApi('', null, null);
              setToken(null);
              setUsername('');
              setServerUrl(DEFAULT_SERVER);
              setCloudflareClientId('');
              setCloudflareClientSecret('');
            } else {
              // Kein Netz/5xx ist keine Abmeldung. Gecachte Rezepte bleiben
              // lesbar und die Session wird beim nächsten Request erneut geprüft.
              setSessionWarning('Server gerade nicht erreichbar – gespeicherte Inhalte werden angezeigt.');
            }
          }
        }
      })
      .catch(async () => {
        await purgeStoredAuth();
        configureApi('', null, null);
        setToken(null);
        setUsername('');
        setServerUrl(DEFAULT_SERVER);
        setCloudflareClientId('');
        setCloudflareClientSecret('');
      })
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(async () => {
      await purgeStoredAuth();
      await clearApiCache();
      configureApi('', null, null);
      setToken(null);
      setUsername('');
      setServerUrl(DEFAULT_SERVER);
      setCloudflareClientId('');
      setCloudflareClientSecret('');
      setSessionWarning('Deine Sitzung ist abgelaufen. Bitte erneut anmelden.');
      router.replace('/login');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

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
    const result = await api<{ token: string; username: string }>('/api/auth/login', {
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
      await purgeStoredAuth();
      configureApi('', null, null);
      throw reason;
    }
    configureApi(normalizedServer, result.token, cloudflare);
    setServerUrl(normalizedServer);
    setCloudflareClientId(normalizedClientId);
    setCloudflareClientSecret(normalizedClientSecret);
    setToken(result.token);
    setUsername(result.username);
    setSessionWarning('');
    router.replace('/(tabs)');
  }

  async function signOut() {
    try {
      if (token) await api('/api/auth/logout', { method: 'POST' });
    } catch {
      // Lokales Abmelden muss auch bei fehlendem Netzwerk immer funktionieren.
    } finally {
      await purgeStoredAuth();
      await clearApiCache();
      await Promise.allSettled([Image.clearMemoryCache(), Image.clearDiskCache()]);
      configureApi('', null, null);
      setToken(null);
      setUsername('');
      setServerUrl(DEFAULT_SERVER);
      setCloudflareClientId('');
      setCloudflareClientSecret('');
      router.replace('/login');
    }
  }

  const value = {
    ready,
    token,
    serverUrl,
    username,
    cloudflareClientId,
    cloudflareClientSecret,
    sessionWarning,
    signIn,
    signOut,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth muss innerhalb von AuthProvider verwendet werden');
  return value;
}
