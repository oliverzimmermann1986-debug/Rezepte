import * as Crypto from 'expo-crypto';
import * as WebBrowser from 'expo-web-browser';
import { api, ApiError, currentApiSessionEpoch, isApiSessionEpochCurrent } from './api';
import { IdentityProvider } from './account-management';

export type LoginResult = { token: string; username: string; role?: string; is_admin?: boolean };
const CALLBACK = 'de.mausbaeren.rezepte://auth/callback';

export async function fetchIdentityProviders(server: string, signal?: AbortSignal): Promise<IdentityProvider[]> {
  const response = await fetch(`${server}/api/auth/providers`, { signal, redirect: 'manual', headers: { Accept: 'application/json' } });
  if (!response.ok || response.redirected) throw new ApiError('Weitere Anmeldemöglichkeiten konnten nicht geladen werden.', response.status);
  const result = await response.json() as { providers?: IdentityProvider[] };
  return (result.providers || []).filter(provider => provider.enabled && ['apple', 'google'].includes(provider.id));
}

export async function providerAuthentication(provider: 'apple' | 'google', intent: 'login' | 'link', invitationToken = '', currentPassword = ''): Promise<LoginResult | null> {
  if (!['apple', 'google'].includes(provider)) throw new ApiError('Unbekannte Anmeldemöglichkeit.', 400);
  const epoch = currentApiSessionEpoch();
  // Keep the verifier in memory only. A terminated app restarts authentication.
  const bytes = await Crypto.getRandomBytesAsync(32);
  const verifier = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  const challenge = (await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, verifier, { encoding: Crypto.CryptoEncoding.BASE64 })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const flow = await api<{ authorization_url: string; flow_id: string }>(`/api/auth/${provider}/start`, {
    method: 'POST', body: JSON.stringify({ platform: 'native', intent, code_challenge: challenge, ...(invitationToken ? { invitation_token: invitationToken } : {}), ...(intent === 'link' && currentPassword ? { current_password: currentPassword } : {}) }),
  });
  const authorization = new URL(flow.authorization_url);
  const expectedHost = provider === 'apple' ? 'appleid.apple.com' : 'accounts.google.com';
  const authority = flow.authorization_url.match(/^https:\/\/([^/?#]+)/i)?.[1].toLowerCase();
  if (authorization.protocol !== 'https:' || authority !== expectedHost || authorization.hostname !== expectedHost || authorization.username || authorization.password || authorization.port || authorization.hash) throw new ApiError('Ungültige Anmeldeadresse vom Server.', 502);
  const result = await WebBrowser.openAuthSessionAsync(authorization.toString(), CALLBACK, { preferEphemeralSession: true });
  if (!isApiSessionEpochCurrent(epoch)) throw new ApiError('Die Anmeldung wurde beendet. Bitte erneut versuchen.', 401);
  if (result.type !== 'success') return null;
  const callback = new URL(result.url);
  const flows = callback.searchParams.getAll('flow_id');
  const codes = callback.searchParams.getAll('code');
  const errors = callback.searchParams.getAll('error');
  if (callback.protocol !== 'de.mausbaeren.rezepte:' || callback.host !== 'auth' || callback.pathname !== '/callback' || callback.username || callback.password || callback.port || callback.hash || flows.length !== 1 || flows[0] !== flow.flow_id) throw new ApiError('Die Anmeldeantwort gehört nicht zu diesem Vorgang.', 400);
  if (!codes.length && errors.length === 1 && errors[0] === 'cancelled') return null;
  if (codes.length !== 1 || errors.length || !/^[A-Za-z0-9_-]{43}$/.test(codes[0])) throw new ApiError('Die Anmeldung konnte nicht abgeschlossen werden. Bitte erneut versuchen.', 400);
  const code = codes[0];
  return api<LoginResult>('/api/auth/exchange', { method: 'POST', body: JSON.stringify({ code, code_verifier: verifier }) });
}
