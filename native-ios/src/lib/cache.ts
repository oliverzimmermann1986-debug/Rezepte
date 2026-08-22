import AsyncStorage from '@react-native-async-storage/async-storage';

import { ApiError, api, apiBaseUrl } from './api';

const PREFIX = 'rezepte.cache.v1:';

type CachedValue<T> = { storedAt: number; value: T };

function storageKey(key: string) {
  return `${PREFIX}${encodeURIComponent(apiBaseUrl())}:${key}`;
}

export async function apiCached<T>(key: string, path: string, signal?: AbortSignal): Promise<T> {
  try {
    const value = await api<T>(path, {}, signal);
    const cached: CachedValue<T> = { storedAt: Date.now(), value };
    await AsyncStorage.setItem(storageKey(key), JSON.stringify(cached));
    return value;
  } catch (reason) {
    if (signal?.aborted) throw reason;
    const recoverable = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
    if (!recoverable) throw reason;
    const raw = await AsyncStorage.getItem(storageKey(key));
    if (!raw) throw reason;
    try {
      return (JSON.parse(raw) as CachedValue<T>).value;
    } catch {
      await AsyncStorage.removeItem(storageKey(key));
      throw reason;
    }
  }
}

export async function invalidateApiCache(...keys: string[]) {
  if (!keys.length) return;
  await AsyncStorage.multiRemove(keys.map(storageKey));
}

export async function clearApiCache() {
  const keys = await AsyncStorage.getAllKeys();
  const ours = keys.filter(key => key.startsWith(PREFIX));
  if (ours.length) await AsyncStorage.multiRemove(ours);
}
