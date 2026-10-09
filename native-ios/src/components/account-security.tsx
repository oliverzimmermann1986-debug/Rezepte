import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useRef, useState } from 'react';
import { Alert, StyleSheet, Text, TextInput, View } from 'react-native';
import { PrimaryButton } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { AccountIdentity, AccountProfile, AccountSession, IdentityProvider, accountRoleLabels, accountDate, passwordProblem } from '@/lib/account-management';

export function AccountSecurity() {
  const { signOut, linkProvider } = useAuth();
  const [profile, setProfile] = useState<AccountProfile | null>(null);
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [identities, setIdentities] = useState<AccountIdentity[]>([]);
  const [providers, setProviders] = useState<IdentityProvider[]>([]);
  const [section, setSection] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const loadGeneration = useRef(0);
  const clearPasswords = () => { setCurrentPassword(''); setNewPassword(''); setConfirmation(''); };
  const load = useCallback(async (signal?: AbortSignal) => {
    const generation = ++loadGeneration.current;
    try {
      const [nextProfile, nextSessions, nextIdentities] = await Promise.all([
        api<AccountProfile>('/api/account/profile', {}, signal),
        api<{ sessions: AccountSession[] }>('/api/account/sessions', {}, signal),
        api<{ identities: AccountIdentity[]; providers: IdentityProvider[] }>('/api/account/identities', {}, signal),
      ]);
      if (signal?.aborted || generation !== loadGeneration.current) return;
      setProfile(nextProfile); setSessions(nextSessions.sessions); setIdentities(nextIdentities.identities); setError('');
      setProviders((nextIdentities.providers || []).filter(provider => provider.enabled && ['apple', 'google'].includes(provider.id)));
    } catch (reason) {
      if (!signal?.aborted && generation === loadGeneration.current) setError(reason instanceof Error ? reason.message : 'Kontodaten konnten nicht geladen werden.');
    }
  }, []);
  useFocusEffect(useCallback(() => {
    const controller = new AbortController(); void load(controller.signal);
    return () => { controller.abort(); ++loadGeneration.current; setCurrentPassword(''); setNewPassword(''); setConfirmation(''); };
  }, [load]));
  async function action(operation: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try { await operation(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Aktion fehlgeschlagen.'); }
    finally { clearPasswords(); inFlight.current = false; setBusy(false); }
  }
  function confirmAction(title: string, message: string, operation: () => Promise<void>) {
    Alert.alert(title, message, [{ text: 'Abbrechen', style: 'cancel' }, { text: 'Bestätigen', style: 'destructive', onPress: () => void action(operation) }]);
  }
  const passwordField = profile?.password_enabled ? <TextInput accessibilityLabel="Aktuelles Passwort" placeholder="Aktuelles Passwort" secureTextEntry textContentType="password" value={currentPassword} onChangeText={setCurrentPassword} editable={!busy} style={styles.input} /> : <Text style={styles.note}>Bitte zuvor erneut mit Apple oder Google anmelden. Die Bestätigung gilt fünf Minuten.</Text>;
  return <View style={styles.card}>
    <Text accessibilityRole="header" style={styles.heading}>Anmeldung &amp; Sicherheit</Text>
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {!!notice && <Text accessibilityRole="alert" style={styles.note}>{notice}</Text>}
    <PrimaryButton label="Kontodaten aktualisieren" onPress={() => void load()} disabled={busy} />
    {profile && <>
      <Text style={styles.note}>{profile.username} · {accountRoleLabels[profile.role]}{ '\n' }Erstellt: {accountDate(profile.created_at)}</Text>
      {[['password', 'Passwort ändern oder einrichten'], ['sessions', 'Angemeldete Geräte'], ['identities', 'Verknüpfte Anmeldungen'], ['delete', 'Konto löschen']].map(([key, label]) => <PrimaryButton key={key} label={(section === key ? '− ' : '+ ') + label} onPress={() => { clearPasswords(); setSection(section === key ? '' : key); }} disabled={busy} />)}
      {section === 'password' && <View style={styles.group}>
        <Text style={styles.note}>Mindestens 10 Zeichen, höchstens 72 UTF-8-Bytes. Danach auf allen Geräten erneut anmelden.</Text>
        {passwordField}
        <TextInput accessibilityLabel="Neues Passwort" placeholder="Neues Passwort" secureTextEntry textContentType="newPassword" value={newPassword} onChangeText={setNewPassword} editable={!busy} style={styles.input} />
        <TextInput accessibilityLabel="Neues Passwort wiederholen" placeholder="Neues Passwort wiederholen" secureTextEntry textContentType="newPassword" value={confirmation} onChangeText={setConfirmation} editable={!busy} style={styles.input} />
        <PrimaryButton label="Passwort speichern & neu anmelden" disabled={busy} onPress={() => void action(async () => {
          const problem = passwordProblem(newPassword, confirmation); if (problem) throw new Error(problem);
          await api('/api/account/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
          await signOut({ localOnly: true, notice: 'Passwort gespeichert. Bitte erneut anmelden.' });
        })} />
      </View>}
      {section === 'sessions' && <View style={styles.group}>
        {sessions.map(item => <View key={item.id} style={styles.group}>
          <Text style={styles.note}>{item.client_label || 'Unbekanntes Gerät'}{item.is_current ? ' · Diese Sitzung' : ''}{'\n'}Zuletzt aktiv: {accountDate(item.last_seen_at)}{'\n'}Gültig bis: {accountDate(item.expires_at)}</Text>
          <PrimaryButton label="Sitzung beenden" disabled={busy} onPress={() => confirmAction('Sitzung beenden?', item.is_current ? 'Du wirst auf diesem Gerät abgemeldet.' : 'Dieses Gerät wird abgemeldet.', async () => {
            await api(`/api/account/sessions/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
            if (item.is_current) await signOut({ localOnly: true }); else { await load(); setNotice('Sitzung beendet.'); }
          })} />
        </View>)}
        {!sessions.length && <Text style={styles.note}>Keine aktiven Sitzungen gefunden.</Text>}
        <PrimaryButton label="Auf allen Geräten abmelden" destructive disabled={busy} onPress={() => confirmAction('Überall abmelden?', 'Alle Sitzungen einschließlich dieser Sitzung werden beendet.', () => signOut({ all: true }))} />
      </View>}
      {section === 'identities' && <View style={styles.group}>
        {!identities.length && <Text style={styles.note}>Keine verknüpften Anmeldungen.</Text>}
        {(!!identities.length || !!providers.length) && passwordField}
        {identities.map(identity => <View key={identity.provider} style={styles.group}>
          <Text style={styles.note}>{identity.provider === 'apple' ? 'Apple' : 'Google'}{identity.email ? ' · ' + identity.email : ''}</Text>
          {providers.some(provider => provider.id === identity.provider) && <PrimaryButton label={`Mit ${identity.provider === 'apple' ? 'Apple' : 'Google'} erneut bestätigen`} disabled={busy} onPress={() => void action(async () => { await linkProvider(identity.provider); await load(); })} />}
          <PrimaryButton label="Verknüpfung trennen" disabled={busy} onPress={() => confirmAction('Anmeldung trennen?', 'Eine andere Anmeldemöglichkeit muss erhalten bleiben.', async () => {
            await api(`/api/account/identities/${identity.provider}`, { method: 'DELETE', body: JSON.stringify({ current_password: currentPassword }) }); await load(); setNotice('Verknüpfung entfernt.');
          })} />
        </View>)}
        {providers.filter(provider => !identities.some(identity => identity.provider === provider.id)).map(provider => <PrimaryButton key={provider.id} label={`${provider.name} verknüpfen`} disabled={busy} onPress={() => void action(async () => { await linkProvider(provider.id, currentPassword); await load(); })} />)}
        {!providers.length && <Text style={styles.note}>Apple und Google sind auf diesem Server derzeit nicht eingerichtet.</Text>}
      </View>}
      {section === 'delete' && <View style={styles.group}>
        <Text style={styles.note}>Dein Konto wird endgültig gelöscht. Ein Haushalt mit Daten benötigt zuerst eine weitere Person. Der letzte Administrator kann nicht gelöscht werden.</Text>
        {passwordField}
        <PrimaryButton label="Mein Konto endgültig löschen" destructive disabled={busy} onPress={() => confirmAction('Konto endgültig löschen?', 'Diese Aktion lässt sich nicht rückgängig machen.', async () => {
          await api('/api/account/profile', { method: 'DELETE', body: JSON.stringify({ current_password: currentPassword }) }); await signOut({ localOnly: true, notice: 'Dein Konto wurde gelöscht.' });
        })} />
      </View>}
    </>}
  </View>;
}
const styles = StyleSheet.create({
  card: { backgroundColor: colors.surface, borderRadius: radii.lg, padding: space.lg, gap: space.md },
  heading: { fontSize: 20, fontWeight: '600', color: colors.text },
  group: { gap: space.md, paddingVertical: space.sm },
  note: { fontSize: 15, lineHeight: 22, color: colors.muted },
  error: { fontSize: 16, color: colors.danger },
  input: { backgroundColor: colors.cream, borderRadius: radii.md, padding: space.md, minHeight: 48, fontSize: 16, color: colors.text },
});
