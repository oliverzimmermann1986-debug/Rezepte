export const accountRoles = ['guest', 'user', 'full_user', 'admin'] as const;
export type AccountRole = typeof accountRoles[number];
export const accountRoleLabels: Record<AccountRole, string> = { guest: 'Gast', user: 'Benutzer', full_user: 'Vollbenutzer', admin: 'Admin' };

export type AccountProfile = {
  id: number; username: string; role: AccountRole; created_at: number;
  last_login_at: number | null; password_enabled: boolean;
};
export type AccountSession = {
  id: string; created_at: number; last_seen_at: number; expires_at: number;
  client_label: string; is_current: boolean;
};
export type ManagedUser = Omit<AccountProfile, 'password_enabled'> & { disabled: boolean };
export type AccountIdentity = { provider: 'apple' | 'google'; email?: string; linked_at: number };
export type IdentityProvider = { id: 'apple' | 'google'; name: string; enabled: boolean };

export function passwordProblem(password: string, confirmation = password): string {
  // Count Unicode scalar values like the server; bcrypt's limit is UTF-8 bytes.
  const bytes = [...password].reduce((sum, character) => {
    const point = character.codePointAt(0)!;
    return sum + (point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4);
  }, 0);
  if ([...password].length < 10 || bytes > 72) return 'Das neue Passwort braucht mindestens 10 Zeichen und darf höchstens 72 UTF-8-Bytes lang sein.';
  if (password !== confirmation) return 'Die neuen Passwörter stimmen nicht überein.';
  return '';
}

export function accountDate(timestamp: number | null): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('de-DE') : 'Noch nie';
}
