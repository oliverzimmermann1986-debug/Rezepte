import { useFocusEffect } from '@react-navigation/native';
import { router } from 'expo-router';
import React, { useCallback, useRef, useState } from 'react';
import { Share, StyleSheet, Text, TextInput, View } from 'react-native';

import { PrimaryButton, Screen } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api, currentApiSessionEpoch, isApiSessionEpochCurrent } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { invalidateApiCacheByPrefix } from '@/lib/cache';

type Account = {
  is_guest: boolean;
  is_owner: boolean;
  members: { id: number; username: string; disabled: boolean }[];
  invitations: { id: number; expires_at: number; revoked_at: number | null; accepted_at: number | null }[];
};
type Invitation = { id: number; token: string; invite_path: string; expires_at: number };
type OwnImport = { url: string; name?: string; suggestion: { type?: string; category?: string; analysis_state?: string; analysis_error?: string;
  ingredients?: unknown[]; steps?: unknown[]; servings?: number | null } };

export default function AccountScreen() {
  const { username, isAdmin, isGuest, serverUrl, cloudflareClientId, cloudflareClientSecret, registerAccount, signOut, returnToLogin, refreshHousehold } = useAuth();
  const [account, setAccount] = useState<Account | null>(null);
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [joinToken, setJoinToken] = useState('');
  const [importUrl, setImportUrl] = useState('');
  const [importNotice, setImportNotice] = useState('');
  const [importVisibility, setImportVisibility] = useState<'private' | 'global'>('private');
  const [imports, setImports] = useState<OwnImport[]>([]);
  const loadGeneration = useRef(0);
  const activeLoad = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    const epoch = currentApiSessionEpoch();
    activeLoad.current?.abort();
    const controller = new AbortController();
    activeLoad.current = controller;
    try {
      const result = await api<Account>('/api/account', {}, controller.signal);
      if (generation !== loadGeneration.current || controller.signal.aborted) return;
      setAccount(result);
      if (!result.is_guest) {
        const pending = await api<{ items: OwnImport[] }>('/api/account/imports', {}, controller.signal);
        if (generation !== loadGeneration.current || controller.signal.aborted) return;
        setImports(pending.items);
      }
      setError('');
    } catch (reason) {
      if (generation !== loadGeneration.current || controller.signal.aborted || !isApiSessionEpochCurrent(epoch)) return;
      setError(reason instanceof Error ? reason.message : 'Konto konnte nicht geladen werden.');
    }
  }, []);
  useFocusEffect(useCallback(() => {
    if (username) void load();
    else { setAccount(null); setImports([]); }
    return () => { activeLoad.current?.abort(); ++loadGeneration.current; };
  }, [load, username]));

  async function action(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try { await operation(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Aktion fehlgeschlagen.'); }
    finally { setBusy(false); }
  }

  function tokenValue() {
    const value = joinToken.trim();
    const parsed = value.includes('://') ? new URL(value).searchParams.get('invite') || '' : value;
    if (value && !parsed) throw new Error('Im Link fehlt der Einladungscode.');
    return parsed;
  }

  async function createAccount() {
    if (password !== confirmation) throw new Error('Passwörter stimmen nicht überein.');
    await registerAccount(serverUrl, name, password, cloudflareClientId, cloudflareClientSecret, tokenValue());
    setPassword('');
    setConfirmation('');
    setInvitation(null);
    await load();
  }

  return (
    <Screen>
      <Text accessibilityRole="header" style={styles.title}>Mein Konto</Text>
      <Text style={styles.note}>{isGuest ? 'Gastzugang · nur ansehen' : username}</Text>
      <Text style={styles.note}>Globale Rezepte sind für alle sichtbar. Private Rezepte, Favoriten, Bewertungen, Einkauf und Wochenplan gehören zu deinem Haushalt.</Text>
      {!!error && <View style={styles.card}><Text accessibilityRole="alert" style={styles.error}>{error}</Text><PrimaryButton label="Erneut laden" onPress={() => void load()} /></View>}
      {isGuest ? (
        <View style={styles.card}>
          <Text style={styles.heading}>Konto erstellen</Text>
          <Text style={styles.note}>Mit einem eigenen Konto kannst du Haushaltsfunktionen nutzen und eine zweite Person einladen.</Text>
          <TextInput accessibilityLabel="Benutzername" placeholder="Benutzername" autoCapitalize="none" autoCorrect={false} autoComplete="username" value={name} onChangeText={setName} editable={!busy} style={styles.input} />
          <TextInput accessibilityLabel="Neues Passwort" placeholder="Passwort · mindestens 10 Zeichen" secureTextEntry textContentType="newPassword" value={password} onChangeText={setPassword} editable={!busy} style={styles.input} />
          <TextInput accessibilityLabel="Passwort wiederholen" placeholder="Passwort wiederholen" secureTextEntry textContentType="newPassword" value={confirmation} onChangeText={setConfirmation} editable={!busy} style={styles.input} />
          <TextInput accessibilityLabel="Einladungscode oder Link" placeholder="Einladungscode oder Link (optional)" autoCapitalize="none" autoCorrect={false} value={joinToken} onChangeText={setJoinToken} editable={!busy} style={styles.input} />
          <PrimaryButton label={busy ? 'Konto wird erstellt …' : 'Konto erstellen'} onPress={() => void action(createAccount)} disabled={busy || name.trim().length < 3 || password.length < 10} />
        </View>
      ) : (
        <View style={styles.card}>
          <Text style={styles.heading}>Personen im Konto</Text>
          {!account && !error && <Text style={styles.note}>Konto wird geladen …</Text>}
          {account?.members.map(member => <Text key={member.id} style={styles.member}>{member.username}{member.disabled ? ' · deaktiviert' : ''}</Text>)}
          {account?.is_owner && account.members.length < 2 && <>
            <Text style={styles.note}>Die zweite Person erhält eine eigene Anmeldung. Der Link gilt sieben Tage, ist einmal verwendbar und ersetzt ältere offene Einladungen.</Text>
            <PrimaryButton label="Zweite Person einladen" onPress={() => void action(async () => { setInvitation(await api<Invitation>('/api/account/invitations', { method: 'POST', body: '{}' })); await load(); })} disabled={busy} />
          </>}
          {invitation && <>
            <Text selectable style={styles.note}>{serverUrl + invitation.invite_path}</Text>
            <PrimaryButton label="Einladungslink teilen" onPress={() => void action(async () => { await Share.share({ message: 'Einladung zu unserem Rezeptkonto: ' + serverUrl + invitation.invite_path }); })} disabled={busy} />
          </>}
          {account?.invitations.filter(item => !item.revoked_at && !item.accepted_at && item.expires_at * 1000 > Date.now()).map(item => (
            <View key={item.id} style={styles.pending}>
              <Text style={styles.note}>Offene Einladung · bis {new Date(item.expires_at * 1000).toLocaleDateString('de-DE')}</Text>
              <PrimaryButton label="Einladung widerrufen" onPress={() => void action(async () => { await api(`/api/account/invitations/${item.id}`, { method: 'DELETE' }); if (invitation?.id === item.id) setInvitation(null); await load(); })} disabled={busy} />
            </View>
          ))}
          {account?.is_owner && account.members.length < 2 && <>
            <Text style={styles.heading}>Einladung erhalten?</Text>
            <Text style={styles.note}>Beim Beitritt werden deine Sammlung, privaten Rezepte, Einkäufe und Planungen in den gemeinsamen Haushalt übernommen.</Text>
            <TextInput accessibilityLabel="Einladungscode oder Link" placeholder="Einladungscode oder Link" autoCapitalize="none" autoCorrect={false} value={joinToken} onChangeText={setJoinToken} editable={!busy} style={styles.input} />
            <PrimaryButton label="Einladung annehmen" onPress={() => void action(async () => { await api('/api/account/invitations/accept', { method: 'POST', body: JSON.stringify({ token: tokenValue() }) }); setJoinToken(''); setInvitation(null); await refreshHousehold(); await load(); router.replace('/(tabs)'); })} disabled={busy || !joinToken.trim()} />
          </>}
        </View>
      )}
      {!isGuest && <View style={styles.card}>
        <Text style={styles.heading}>Rezept hinzufügen</Text>
        <Text style={styles.note}>Private Importe bleiben im Haushalt. Bereits globale Links werden als Verweis in deiner Sammlung gespeichert.</Text>
        <TextInput accessibilityLabel="Rezeptlink" placeholder="https://…" keyboardType="url" autoCapitalize="none" autoCorrect={false} value={importUrl} onChangeText={setImportUrl} editable={!busy} style={styles.input} />
        {isAdmin && <PrimaryButton label={importVisibility === 'private' ? 'Sichtbarkeit: privat im Haushalt' : 'Sichtbarkeit: global für alle'}
                                  onPress={() => setImportVisibility(current => current === 'private' ? 'global' : 'private')} disabled={busy} />}
        <PrimaryButton label="In Sammlung übernehmen" disabled={busy || !importUrl.trim()} onPress={() => void action(async () => {
          const result = await api<{ message?: string; recipe_id?: number }>('/api/pending/import-url', { method: 'POST', body: JSON.stringify({ url: importUrl.trim(), type: 'recipe', visibility: importVisibility }) });
          setImportNotice(result.message || 'Link übernommen.'); setImportUrl(''); await invalidateApiCacheByPrefix('recipes:', 'recipe:');
          await load();
          if (result.recipe_id) router.push({ pathname: '/recipe/[id]', params: { id: String(result.recipe_id) } });
        })} />
        {!!importNotice && <Text accessibilityRole="alert" style={styles.note}>{importNotice}</Text>}
      </View>}
      {!isGuest && imports.length > 0 && <View style={styles.card}>
        <Text style={styles.heading}>Private Importe prüfen</Text>
        <PrimaryButton label="Status aktualisieren" onPress={() => void load()} disabled={busy} />
        {imports.map(item => <View key={item.url} style={styles.pending}>
          <Text style={styles.note}>{['queued', 'running'].includes(item.suggestion.analysis_state || '') ? 'Wird analysiert …' : 'Bitte prüfen und übernehmen'}</Text>
          {!!item.suggestion.analysis_error && <Text style={styles.error}>{item.suggestion.analysis_error}</Text>}
          <TextInput accessibilityLabel="Name des privaten Rezepts" value={item.name || ''} onChangeText={value => setImports(current => current.map(row => row.url === item.url ? { ...row, name: value } : row))}
                     style={styles.input} editable={!busy} />
          <Text style={styles.note}>{(item.suggestion.ingredients || []).length} Zutaten · {(item.suggestion.steps || []).length} Schritte</Text>
          <PrimaryButton label="Rezept übernehmen" disabled={busy || !item.name?.trim() || ['queued', 'running'].includes(item.suggestion.analysis_state || '')}
            onPress={() => void action(async () => {
              const result = await api<{ ok: boolean; error?: string }>('/api/pending', { method: 'POST', body: JSON.stringify({ url: item.url, visibility: 'private', action: 'save', name: item.name?.trim(),
                type: item.suggestion.type || 'Sonstiges', category: item.suggestion.category || 'Allgemein', ingredients: item.suggestion.ingredients || [],
                steps: item.suggestion.steps || [], servings: item.suggestion.servings || null }) });
              if (!result.ok) throw new Error(result.error || 'Rezept konnte nicht übernommen werden');
              await invalidateApiCacheByPrefix('recipes:'); await load();
            })} />
        </View>)}
      </View>}
      <PrimaryButton label={isGuest ? 'Zur Anmeldung' : 'Abmelden'} onPress={() => void (isGuest ? returnToLogin() : signOut())} disabled={busy} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 30, fontWeight: '700', color: colors.text },
  heading: { fontSize: 20, fontWeight: '600', color: colors.text },
  note: { fontSize: 15, lineHeight: 22, color: colors.muted },
  member: { fontSize: 17, color: colors.text, paddingVertical: 5 },
  card: { backgroundColor: colors.surface, borderRadius: radii.lg, padding: space.lg, gap: space.md },
  pending: { gap: space.sm },
  error: { fontSize: 16, color: colors.danger },
  input: { backgroundColor: colors.cream, borderRadius: radii.md, padding: space.md, fontSize: 16, color: colors.text, minHeight: 48 },
});
