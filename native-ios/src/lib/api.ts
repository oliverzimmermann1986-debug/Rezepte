import Constants from 'expo-constants';
import * as FileSystem from 'expo-file-system/legacy';

let authToken: string | null = null;
let configuredUrl = '';
let cloudflareClientId = '';
let cloudflareClientSecret = '';
let unauthorizedHandler: (() => void | Promise<void>) | null = null;
let unauthorizedHandling = false;
const REQUEST_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 90_000;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export function configureApi(
  baseUrl: string,
  token: string | null,
  cloudflare?: { clientId: string; clientSecret: string } | null,
) {
  configuredUrl = baseUrl.trim().replace(/\/+$/, '');
  authToken = token;
  cloudflareClientId = cloudflare?.clientId.trim() || '';
  cloudflareClientSecret = cloudflare?.clientSecret.trim() || '';
}

export function setUnauthorizedHandler(handler: (() => void | Promise<void>) | null) {
  unauthorizedHandler = handler;
}

async function notifyUnauthorized() {
  if (!unauthorizedHandler || unauthorizedHandling) return;
  unauthorizedHandling = true;
  try {
    await unauthorizedHandler();
  } finally {
    unauthorizedHandling = false;
  }
}

export function apiBaseUrl() {
  return configuredUrl || String(Constants.expoConfig?.extra?.apiUrl || '').replace(/\/+$/, '');
}

export function absoluteApiUrl(path: string) {
  return `${apiBaseUrl()}${path.startsWith('/') ? path : `/${path}`}`;
}

export function apiAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  if (cloudflareClientId && cloudflareClientSecret) {
    headers['CF-Access-Client-Id'] = cloudflareClientId;
    headers['CF-Access-Client-Secret'] = cloudflareClientSecret;
  }
  return headers;
}

async function readResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();
  if (!contentType.includes('application/json')) {
    const body = String(payload);
    const isCloudflare = response.url.includes('cloudflareaccess.com')
      || body.includes('Cloudflare Access')
      || body.includes('cloudflareaccess.com');
    throw new ApiError(
      isCloudflare
        ? 'Cloudflare Access verlangt eine gültige Client-ID und ein Client-Secret.'
        : 'Der Server hat keine gültige JSON-Antwort gesendet.',
      response.status,
    );
  }
  if (!response.ok) {
    const message = typeof payload === 'object' && payload?.detail
      ? String(payload.detail)
      : `Serverfehler (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return payload as T;
}

async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  externalSignal: AbortSignal | undefined,
  timeoutMs: number,
) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  externalSignal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (reason) {
    if (controller.signal.aborted && !externalSignal?.aborted) {
      throw new ApiError('Der Server antwortet nicht rechtzeitig. Bitte erneut versuchen.', 0);
    }
    throw reason;
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener('abort', abort);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const baseUrl = apiBaseUrl();
  if (!baseUrl) throw new ApiError('Bitte zuerst die Server-Adresse eintragen.', 0);
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (cloudflareClientId && cloudflareClientSecret) {
    headers.set('CF-Access-Client-Id', cloudflareClientId);
    headers.set('CF-Access-Client-Secret', cloudflareClientSecret);
  }
  const response = await fetchWithTimeout(
    absoluteApiUrl(path),
    { ...options, headers },
    signal,
    REQUEST_TIMEOUT_MS,
  );
  if (response.status === 401 && path !== '/api/auth/login') await notifyUnauthorized();
  return readResponse<T>(response);
}

export async function uploadFile<T>(
  path: string,
  file: { uri: string; name: string; mimeType: string },
): Promise<T> {
  const baseUrl = apiBaseUrl();
  if (!baseUrl) throw new ApiError('Bitte zuerst die Server-Adresse eintragen.', 0);
  const body = new FormData();
  body.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType,
  } as unknown as Blob);
  const headers = new Headers({ Accept: 'application/json' });
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (cloudflareClientId && cloudflareClientSecret) {
    headers.set('CF-Access-Client-Id', cloudflareClientId);
    headers.set('CF-Access-Client-Secret', cloudflareClientSecret);
  }
  const response = await fetchWithTimeout(
    absoluteApiUrl(path),
    { method: 'POST', headers, body },
    undefined,
    UPLOAD_TIMEOUT_MS,
  );
  if (response.status === 401) await notifyUnauthorized();
  return readResponse<T>(response);
}

export async function downloadFileToCache(path: string, filename: string) {
  if (!FileSystem.cacheDirectory) throw new ApiError('Lokaler Dateispeicher ist nicht verfügbar.', 0);
  const safeName = filename.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+/, '') || 'dokument.pdf';
  const destination = `${FileSystem.cacheDirectory}${Date.now()}-${safeName}`;
  const result = await FileSystem.downloadAsync(absoluteApiUrl(path), destination, {
    headers: { Accept: 'application/pdf', ...apiAuthHeaders() },
  });
  if (result.status < 200 || result.status >= 300) {
    await FileSystem.deleteAsync(destination, { idempotent: true });
    throw new ApiError(`PDF konnte nicht geladen werden (${result.status}).`, result.status);
  }
  return destination;
}

export async function deleteCachedFile(uri: string) {
  await FileSystem.deleteAsync(uri, { idempotent: true });
}
