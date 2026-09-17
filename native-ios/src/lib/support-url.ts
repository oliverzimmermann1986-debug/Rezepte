export const PUBLIC_SUPPORT_URL = 'https://support.zimlab.org/?module=rezeptregal';

// App support is independent of the recipe server and never forwards credentials.
export function publicSupportUrl(_server: string): string {
  return PUBLIC_SUPPORT_URL;
}
