const SUPPORTED_HOSTS = new Set([
  'instagram.com',
  'www.instagram.com',
  'm.tiktok.com',
  'tiktok.com',
  'vm.tiktok.com',
  'vt.tiktok.com',
  'www.tiktok.com',
]);

const URL_CANDIDATE = /https:\/\/[^\s<>"']+/giu;

function withoutTrailingPunctuation(value: string) {
  return value.replace(/[),.;!?\]}]+$/u, '');
}

export function socialLinkFromSharedContent(webUrl?: string | null, text?: string | null) {
  const candidates = [webUrl || '', ...(text?.match(URL_CANDIDATE) || [])];
  for (const candidate of candidates) {
    try {
      const parsed = new URL(withoutTrailingPunctuation(candidate.trim()));
      if (
        parsed.protocol === 'https:'
        && !parsed.username
        && !parsed.password
        && SUPPORTED_HOSTS.has(parsed.hostname.toLowerCase())
      ) {
        parsed.hash = '';
        return parsed.toString();
      }
    } catch {
      // TikTok teilt oft einen Begleittext; nicht passende Teile werden ignoriert.
    }
  }
  return null;
}
