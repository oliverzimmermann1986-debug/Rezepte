import type { ShareIntent } from 'expo-share-intent';

const URL_CANDIDATE = /https:\/\/[^\s<>"']+/giu;

function withoutTrailingPunctuation(value: string) {
  return value.replace(/[),.;!?\]}]+$/u, '');
}

function isObviouslyPrivateHost(hostname: string) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/gu, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')) return true;
  if (/^(127\.|10\.|192\.168\.|169\.254\.)/u.test(host)) return true;
  const match = host.match(/^172\.(\d+)\./u);
  return !!match && Number(match[1]) >= 16 && Number(match[1]) <= 31;
}

export function socialLinkFromSharedContent(
  webUrl?: string | null,
  text?: string | null,
  metadata: readonly string[] = [],
) {
  const candidates = [
    webUrl || '',
    ...(text?.match(URL_CANDIDATE) || []),
    ...metadata.flatMap(value => value.match(URL_CANDIDATE) || []),
  ];
  for (const candidate of candidates) {
    try {
      const parsed = new URL(withoutTrailingPunctuation(candidate.trim()));
      if (
        parsed.protocol === 'https:'
        && !parsed.username
        && !parsed.password
        && !isObviouslyPrivateHost(parsed.hostname)
      ) {
        parsed.hash = '';
        return parsed.toString();
      }
    } catch {
      // Geteilte Apps liefern oft Begleittext; nicht passende Teile werden ignoriert.
    }
  }
  return null;
}

export function socialLinkFromShareIntent(shareIntent: ShareIntent) {
  const metadata = Object.values(shareIntent.meta || {})
    .filter((value): value is string => typeof value === 'string');
  return socialLinkFromSharedContent(
    shareIntent.webUrl,
    shareIntent.text,
    metadata,
  );
}
