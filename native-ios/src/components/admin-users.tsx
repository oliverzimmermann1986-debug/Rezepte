import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Modal, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { PrimaryButton } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { AccountRole, ManagedUser, accountRoles, accountRoleLabels, accountDate, passwordProblem } from '@/lib/account-management';

type Draft = { id: number | null; username: string; role: AccountRole; disabled: boolean; password: string };

export function AdminUsers({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const { isAdmin, username, signOut } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    if (!isAdmin) return;
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const result = await api<{ users: ManagedUser[] }>('/api/users', {}, signal);
      if (!signal?.aborted && request === generation.current) setUsers(result.users);
    } catch (reason) {
      if (!signal?.aborted && request === generation.current) setError(reason instanceof Error ? reason.message : 'Benutzer konnten nicht geladen werden.');
    } finally { if (request === generation.current) setLoading(false); }
  }, [isAdmin]);
  const invalidateLoads = useCallback(() => { ++generation.current; }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (visible && isAdmin) void load(controller.signal);
    else { setUsers([]); setDraft(null); setError(''); setNotice(''); }
    return () => { controller.abort(); invalidateLoads(); };
  }, [visible, isAdmin, load, invalidateLoads]);
  async function action(operation: () => Promise<void>) {
    if (!isAdmin || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try { await operation(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Aktion fehlgeschlagen.'); }
    finally { setDraft(current => current ? { ...current, password: '' } : null); inFlight.current = false; setBusy(false); }
  }
  function confirm(title: string, message: string, operation: () => Promise<void>) {
    Alert.alert(title, message, [{ text: 'Abbrechen', style: 'cancel' }, { text: 'Bestätigen', style: 'destructive', onPress: () => void action(operation) }]);
  }
  async function save() {
    if (!draft) return;
    if (!draft.id && !/^[a-zA-Z0-9_.-]{3,32}$/.test(draft.username.trim())) throw new Error('Benutzername: 3–32 Buchstaben, Ziffern, Punkt, Bindestrich oder Unterstrich.');
    if (!draft.id || draft.password) { const problem = passwordProblem(draft.password); if (problem) throw new Error(problem); }
    const body = draft.id ? { role: draft.role, disabled: draft.disabled, ...(draft.password ? { password: draft.password } : {}) } : { username: draft.username.trim(), password: draft.password, role: draft.role };
    await api(draft.id ? `/api/users/${draft.id}` : '/api/users', { method: draft.id ? 'PATCH' : 'POST', body: JSON.stringify(body) });
    setDraft(null);
    if (draft.id && draft.username === username && (draft.password || draft.role !== 'admin')) await signOut({ localOnly: true, notice: 'Kontodaten geändert. Bitte erneut anmelden.' });
    else { await load(); setNotice(draft.id ? 'Benutzer gespeichert.' : 'Benutzer erstellt.'); }
  }
  if (!isAdmin) return null;
  return <Modal visible={visible} animationType="slide" onRequestClose={() => { if (!busy) onClose(); }}>
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text accessibilityRole="header" style={styles.title}>Benutzerverwaltung</Text>
        <PrimaryButton label="Schließen" onPress={onClose} disabled={busy} />
        <Text style={styles.note}>Gäste lesen Rezepte. Benutzer nutzen Rezepte und ihren Haushalt. Vollbenutzer dürfen zusätzlich importieren. Admins verwalten auch Konten und das System.</Text>
        {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
        {!!notice && <Text accessibilityRole="alert" style={styles.note}>{notice}</Text>}
        <PrimaryButton label={loading ? 'Wird geladen …' : 'Aktualisieren'} onPress={() => void load()} disabled={loading || busy} />
        <PrimaryButton label="Benutzer erstellen" onPress={() => { setDraft({ id: null, username: '', role: 'user', disabled: false, password: '' }); setError(''); setNotice(''); }} disabled={busy} />
        <TextInput accessibilityLabel="Benutzer suchen" placeholder="Benutzer suchen" autoCapitalize="none" autoCorrect={false} value={query} onChangeText={setQuery} style={styles.input} />
        {draft && <View style={styles.card}>
          <Text style={styles.heading}>{draft.id ? 'Benutzer bearbeiten' : 'Neuer Benutzer'}</Text>
          <TextInput accessibilityLabel="Benutzername" placeholder="Benutzername" autoCapitalize="none" autoCorrect={false} value={draft.username} onChangeText={value => setDraft({ ...draft, username: value })} editable={!draft.id && !busy} maxLength={32} style={styles.input} />
          <Text style={styles.note}>Rolle: {accountRoleLabels[draft.role]}</Text>
          <View style={styles.roles}>{accountRoles.map(role => <PrimaryButton key={role} label={accountRoleLabels[role]} disabled={busy || draft.role === role} onPress={() => setDraft({ ...draft, role })} />)}</View>
          {!!draft.id && <View style={styles.row}><Text style={styles.note}>Konto deaktiviert</Text><Switch accessibilityLabel="Konto deaktiviert" value={draft.disabled} disabled={busy || draft.username === username} onValueChange={value => setDraft({ ...draft, disabled: value })} /></View>}
          <TextInput accessibilityLabel={draft.id ? 'Neues Passwort optional' : 'Passwort'} placeholder={draft.id ? 'Neues Passwort (optional)' : 'Passwort'} secureTextEntry textContentType="newPassword" value={draft.password} onChangeText={value => setDraft({ ...draft, password: value })} editable={!busy} style={styles.input} />
          <Text style={styles.note}>Mindestens 10 Zeichen, höchstens 72 UTF-8-Bytes. Geänderte Rechte oder Passwörter beenden bestehende Sitzungen.</Text>
          <PrimaryButton label="Speichern" disabled={busy} onPress={() => {
            if (draft.id && draft.username === username && (draft.password || draft.role !== 'admin')) confirm('Eigene Sitzung beenden?', 'Diese Änderung erfordert eine erneute Anmeldung.', save);
            else void action(save);
          }} />
          <PrimaryButton label="Bearbeitung abbrechen" onPress={() => setDraft(null)} disabled={busy} />
        </View>}
        {users.filter(item => item.username.toLowerCase().includes(query.trim().toLowerCase())).map(item => <View key={item.id} style={styles.card}>
          <Text style={styles.heading}>{item.username}{item.username === username ? ' · Du' : ''}</Text>
          <Text style={styles.note}>{accountRoleLabels[item.role]} · {item.disabled ? 'deaktiviert' : 'aktiv'}{'\n'}Erstellt: {accountDate(item.created_at)}{'\n'}Letzte Anmeldung: {accountDate(item.last_login_at)}</Text>
          <PrimaryButton label="Bearbeiten" disabled={busy} onPress={() => { setDraft({ id: item.id, username: item.username, role: item.role, disabled: item.disabled, password: '' }); setError(''); setNotice(''); }} />
          <PrimaryButton label="Alle Sitzungen beenden" disabled={busy} onPress={() => confirm('Alle Sitzungen beenden?', `Alle Geräte von „${item.username}“ werden abgemeldet.`, async () => {
            await api(`/api/users/${item.id}/revoke-sessions`, { method: 'POST' });
            if (item.username === username) await signOut({ localOnly: true }); else setNotice('Alle Sitzungen dieses Benutzers beendet.');
          })} />
          <PrimaryButton label="Benutzer löschen" destructive disabled={busy || item.username === username} onPress={() => confirm('Benutzer endgültig löschen?', `„${item.username}“ wird gelöscht. Vorhandene Haushaltsdaten können die Löschung verhindern.`, async () => {
            await api(`/api/users/${item.id}`, { method: 'DELETE' }); setDraft(null); await load(); setNotice('Benutzer gelöscht.');
          })} />
        </View>)}
        {!loading && !error && !users.some(item => item.username.toLowerCase().includes(query.trim().toLowerCase())) && <Text style={styles.note}>Keine passenden Benutzer.</Text>}
      </ScrollView>
    </SafeAreaView>
  </Modal>;
}
const styles = StyleSheet.create({
  roles: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  safe: { flex: 1, backgroundColor: colors.cream }, content: { padding: space.lg, gap: space.md },
  title: { fontSize: 28, fontWeight: '700', color: colors.text }, heading: { fontSize: 20, fontWeight: '600', color: colors.text },
  card: { padding: space.lg, borderRadius: radii.lg, backgroundColor: colors.surface, gap: space.md },
  note: { fontSize: 15, lineHeight: 22, color: colors.muted }, error: { color: colors.danger, fontSize: 16 },
  input: { backgroundColor: colors.cream, color: colors.text, padding: space.md, borderRadius: radii.md, fontSize: 16, minHeight: 48 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
});
