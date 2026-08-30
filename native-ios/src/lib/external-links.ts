import { Linking } from 'react-native';

export function normalizedExternalUrl(value?: string | null) {
  if (!value) return null;
  try {
    const parsed = new URL(value.trim());
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

export function externalSourceLabel(value?: string | null) {
  const normalized = normalizedExternalUrl(value);
  if (!normalized) return 'Quelle';
  const host = new URL(normalized).hostname.toLowerCase();
  if (host === 'instagram.com' || host.endsWith('.instagram.com')) return 'Instagram';
  if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'TikTok';
  if (host === 'youtube.com' || host.endsWith('.youtube.com') || host === 'youtu.be') return 'YouTube';
  if (host === 'pinterest.com' || host.endsWith('.pinterest.com') || host === 'pin.it') return 'Pinterest';
  return 'Webseite';
}

export async function openExternalUrl(value?: string | null) {
  const normalized = normalizedExternalUrl(value);
  if (!normalized) throw new Error('Der Link ist keine gültige HTTPS-Adresse.');
  await Linking.openURL(normalized);
}
