import Constants from 'expo-constants';
import * as FileSystem from 'expo-file-system/legacy';

let authToken: string | null = null;
let configuredUrl = '';
let cloudflareClientId = '';
let cloudflareClientSecret = '';
let sessionEpoch = 0;
let unauthorizedHandler: ((requestEpoch: number) => void | Promise<void>) | null = null;
let unauthorizedHandlingEpoch: number | null = null;
let nextDownloadId = 0;
const REQUEST_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 90_000;
const DOWNLOAD_TIMEOUT_MS = 90_000;

type ActiveDownload = {
  epoch: number;
  destination: string;
  task: FileSystem.DownloadResumable;
};

const activeDownloads = new Map<number, ActiveDownload>();

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
  sessionEpoch += 1;
  cancelDownloadsFromPreviousSessions();
  configuredUrl = baseUrl.trim().replace(/\/+$/, '');
  authToken = token;
  cloudflareClientId = cloudflare?.clientId.trim() || '';
  cloudflareClientSecret = cloudflare?.clientSecret.trim() || '';
}

export function currentApiSessionEpoch() {
  return sessionEpoch;
}

export function isApiSessionEpochCurrent(epoch: number) {
  return epoch === sessionEpoch;
}

export function assertApiSessionEpochCurrent(epoch: number) {
  if (!isApiSessionEpochCurrent(epoch)) {
    throw new ApiError('Die Sitzung wurde beendet.', 401);
  }
}

function cancelDownloadsFromPreviousSessions() {
  for (const [downloadId, download] of activeDownloads) {
    if (download.epoch === sessionEpoch) continue;
    activeDownloads.delete(downloadId);
    void download.task.cancelAsync()
      .catch(() => undefined)
      .finally(() => FileSystem.deleteAsync(
        download.destination,
        { idempotent: true },
      ).catch(() => undefined));
  }
}

export function setUnauthorizedHandler(
  handler: ((requestEpoch: number) => void | Promise<void>) | null,
) {
  unauthorizedHandler = handler;
}

async function notifyUnauthorized(requestEpoch: number) {
  if (
    !unauthorizedHandler
    || !isApiSessionEpochCurrent(requestEpoch)
    || unauthorizedHandlingEpoch === requestEpoch
  ) return;
  unauthorizedHandlingEpoch = requestEpoch;
  try {
    await unauthorizedHandler(requestEpoch);
  } finally {
    if (unauthorizedHandlingEpoch === requestEpoch) unauthorizedHandlingEpoch = null;
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

async function withTimeout<T>(
  externalSignal: AbortSignal | undefined,
  timeoutMs: number,
  operation: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, timeoutMs);
  try {
    return await operation(controller.signal);
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
  const requestEpoch = currentApiSessionEpoch();
  const requestHadAuth = Boolean(authToken);
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
  return withTimeout(
    signal,
    REQUEST_TIMEOUT_MS,
    async timeoutSignal => {
      const response = await fetch(absoluteApiUrl(path), {
        ...options,
        headers,
        signal: timeoutSignal,
      });
      if (response.status === 401 && requestHadAuth && path !== '/api/auth/login') {
        await notifyUnauthorized(requestEpoch);
      }
      return readResponse<T>(response);
    },
  );
}

export function createClientRequestId() {
  const random = Math.random().toString(36).slice(2, 12);
  return `ios-${Date.now().toString(36)}-${random}`;
}

export async function uploadFile<T>(
  path: string,
  file: { uri: string; name: string; mimeType: string },
  clientRequestId = createClientRequestId(),
): Promise<T> {
  const requestEpoch = currentApiSessionEpoch();
  const requestHadAuth = Boolean(authToken);
  const baseUrl = apiBaseUrl();
  if (!baseUrl) throw new ApiError('Bitte zuerst die Server-Adresse eintragen.', 0);
  const body = new FormData();
  body.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType,
  } as unknown as Blob);
  body.append('client_request_id', clientRequestId);
  const headers = new Headers({ Accept: 'application/json' });
  headers.set('Idempotency-Key', clientRequestId);
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  if (cloudflareClientId && cloudflareClientSecret) {
    headers.set('CF-Access-Client-Id', cloudflareClientId);
    headers.set('CF-Access-Client-Secret', cloudflareClientSecret);
  }
  return withTimeout(
    undefined,
    UPLOAD_TIMEOUT_MS,
    async timeoutSignal => {
      const response = await fetch(absoluteApiUrl(path), {
        method: 'POST',
        headers,
        body,
        signal: timeoutSignal,
      });
      if (response.status === 401 && requestHadAuth) await notifyUnauthorized(requestEpoch);
      return readResponse<T>(response);
    },
  );
}

async function downloadWithTimeout(task: FileSystem.DownloadResumable) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      // Der Timeout darf die UI sofort freigeben. cancelAsync läuft parallel;
      // ein späteres Resolve/Reject von downloadAsync bleibt durch Promise.race
      // behandelt und erzeugt keine unhandled rejection.
      void task.cancelAsync().catch(() => undefined);
      reject(new ApiError(
        'Der Dateidownload dauert zu lange. Bitte erneut versuchen.',
        0,
      ));
    }, DOWNLOAD_TIMEOUT_MS);
  });
  try {
    return await Promise.race([task.downloadAsync(), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function downloadFileToCache(
  path: string,
  filename: string,
  accept = 'application/pdf',
) {
  const requestEpoch = currentApiSessionEpoch();
  const requestHadAuth = Boolean(authToken);
  if (!FileSystem.cacheDirectory) throw new ApiError('Lokaler Dateispeicher ist nicht verfügbar.', 0);
  const safeName = filename.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+/, '') || 'dokument.pdf';
  const downloadId = ++nextDownloadId;
  const destination = `${FileSystem.cacheDirectory}${Date.now()}-${downloadId}-${safeName}`;
  const task = FileSystem.createDownloadResumable(
    absoluteApiUrl(path),
    destination,
    { headers: { Accept: accept, ...apiAuthHeaders() } },
  );
  activeDownloads.set(downloadId, { epoch: requestEpoch, destination, task });
  try {
    const result = await downloadWithTimeout(task);
    assertApiSessionEpochCurrent(requestEpoch);
    if (!result) throw new ApiError('Dateidownload wurde abgebrochen.', 0);
    if (result.status === 401 && requestHadAuth) await notifyUnauthorized(requestEpoch);
    if (result.status < 200 || result.status >= 300) {
      throw new ApiError(`Datei konnte nicht geladen werden (${result.status}).`, result.status);
    }
    return destination;
  } catch (reason) {
    await FileSystem.deleteAsync(destination, { idempotent: true }).catch(() => undefined);
    if (!isApiSessionEpochCurrent(requestEpoch)) {
      throw new ApiError('Die Sitzung wurde beendet.', 401);
    }
    throw reason;
  } finally {
    activeDownloads.delete(downloadId);
  }
}

export async function deleteCachedFile(uri: string) {
  await FileSystem.deleteAsync(uri, { idempotent: true });
}
