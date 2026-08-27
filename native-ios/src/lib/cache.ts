import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  ApiError,
  api,
  apiCacheNamespace,
  assertApiSessionEpochCurrent,
  currentApiSessionEpoch,
} from './api';

const PREFIX = 'rezepte.cache.v1:';

type CachedValue<T> = { storedAt: number; value: T };

function storageKey(key: string) {
  return `${PREFIX}${encodeURIComponent(apiCacheNamespace())}:${key}`;
}

function storageNamespace() {
  return `${PREFIX}${encodeURIComponent(apiCacheNamespace())}:`;
}

export async function putApiCache<T>(key: string, value: T) {
  const cached: CachedValue<T> = { storedAt: Date.now(), value };
  try {
    await AsyncStorage.setItem(storageKey(key), JSON.stringify(cached));
  } catch {
    // Der Cache ist nur eine Komfortschicht. Ein lokaler Speicherfehler darf
    // weder eine erfolgreiche Serverantwort noch eine Mutation fehlschlagen lassen.
  }
}

export async function readApiCache<T>(key: string): Promise<T | null> {
  try {
    const raw = await AsyncStorage.getItem(storageKey(key));
    if (!raw) return null;
    return (JSON.parse(raw) as CachedValue<T>).value;
  } catch {
    // Beschädigte oder nicht lesbare Komfortdaten werden wie ein Cache-Miss
    // behandelt. Der eigentliche Serverfluss bleibt davon unabhängig.
    return null;
  }
}

export async function apiCached<T>(key: string, path: string, signal?: AbortSignal): Promise<T> {
  const requestEpoch = currentApiSessionEpoch();
  const requestStorageKey = storageKey(key);
  try {
    const value = await api<T>(path, {}, signal);
    assertApiSessionEpochCurrent(requestEpoch);
    const cached: CachedValue<T> = { storedAt: Date.now(), value };
    await AsyncStorage.setItem(requestStorageKey, JSON.stringify(cached)).catch(() => undefined);
    assertApiSessionEpochCurrent(requestEpoch);
    return value;
  } catch (reason) {
    if (signal?.aborted) throw reason;
    const recoverable = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
    if (!recoverable) throw reason;
    let raw: string | null;
    try {
      assertApiSessionEpochCurrent(requestEpoch);
      raw = await AsyncStorage.getItem(requestStorageKey);
    } catch {
      throw reason;
    }
    if (!raw) throw reason;
    try {
      return (JSON.parse(raw) as CachedValue<T>).value;
    } catch {
      await AsyncStorage.removeItem(storageKey(key)).catch(() => undefined);
      throw reason;
    }
  }
}

export async function invalidateApiCache(...keys: string[]) {
  if (!keys.length) return;
  try {
    await AsyncStorage.multiRemove(keys.map(storageKey));
  } catch {
    // Eine veraltete Cachezeile ist weniger schlimm als eine fälschlich als
    // fehlgeschlagen gemeldete, serverseitig bereits abgeschlossene Aktion.
  }
}

export async function invalidateApiCacheByPrefix(...prefixes: string[]) {
  if (!prefixes.length) return;
  try {
    const namespace = storageNamespace();
    const keys = await AsyncStorage.getAllKeys();
    const matches = keys.filter(key => (
      key.startsWith(namespace)
      && prefixes.some(prefix => key.slice(namespace.length).startsWith(prefix))
    ));
    if (matches.length) await AsyncStorage.multiRemove(matches);
  } catch {
    // Siehe invalidateApiCache: Cachepflege bleibt best effort.
  }
}

export async function clearApiCache() {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const ours = keys.filter(key => key.startsWith(PREFIX));
    if (ours.length) await AsyncStorage.multiRemove(ours);
  } catch {
    // Auch ein kompletter Cache-Reset ist best effort. Insbesondere darf ein
    // lokaler Speicherfehler eine serverseitig erfolgreiche Anmeldung nicht
    // nachträglich verwerfen.
  }
}
