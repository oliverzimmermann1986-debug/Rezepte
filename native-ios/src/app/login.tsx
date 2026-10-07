import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { openExternalUrl } from '@/lib/external-links';
import { IdentityProvider, passwordProblem } from '@/lib/account-management';

export default function LoginScreen() {
  const {
    serverUrl: storedServer,
    sessionWarning,
    authCleanupPending,
    signIn,
    signInAsGuest,
    registerAccount,
    retryAuthCleanup,
    loadProviders,
    signInWithProvider,
  } = useAuth();
  const [server, setServer] = useState(storedServer);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [registration, setRegistration] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [invitationToken, setInvitationToken] = useState('');
  const [providers, setProviders] = useState<IdentityProvider[]>([]);
  const [providersError, setProvidersError] = useState(false);
  const [providersRetry, setProvidersRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setProviders([]); setProvidersError(false);
    const timer = setTimeout(() => {
      void loadProviders(server, controller.signal).then(items => { if (!controller.signal.aborted) setProviders(items); }).catch(() => { if (!controller.signal.aborted) setProvidersError(true); });
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
    // Auth context methods are recreated on state updates; reload only when the
    // selected server changes or the user explicitly retries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server, providersRetry]);

  async function submit() {
    if (busy || !server.trim() || !username.trim() || !password) return;
    setBusy(true);
    setError('');
    try {
      if (registration) {
        const problem = passwordProblem(password, confirmation); if (problem) throw new ApiError(problem, 0);
        const invite = invitationToken.trim();
        const token = invite.includes('://') ? new URL(invite).searchParams.get('invite') || '' : invite;
        if (invite && !token) throw new ApiError('Im Link fehlt der Einladungscode.', 0);
        await registerAccount(server, username, password, token);
      } else await signIn(server, username, password);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Verbindung zum Server fehlgeschlagen.');
    } finally {
      setPassword(''); setConfirmation('');
      setBusy(false);
    }
  }

  async function openPrivacy() {
    try {
      const parsed = new URL(server.trim());
      if (parsed.protocol !== 'https:') throw new Error();
      await openExternalUrl(`${parsed.origin}/privacy`);
    } catch {
      setError('Für den Datenschutz-Link wird eine gültige HTTPS-Serveradresse benötigt.');
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={styles.keyboard}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled">
          <View style={styles.mark}><Text style={styles.markText}>R</Text></View>
          <View style={styles.intro}>
            <Text accessibilityRole="header" style={styles.title}>Rezepte</Text>
            <Text style={styles.subtitle}>Deine private Rezeptbibliothek – nativ auf dem iPhone.</Text>
          </View>
          <View style={styles.form}>
          <TextInput
            accessibilityLabel="Server-Adresse"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            placeholder="https://rezepte.example.de"
            placeholderTextColor={colors.muted}
            value={server}
            onChangeText={setServer}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="Benutzername"
            autoCapitalize="none"
            textContentType="username"
            placeholder="Benutzername"
            placeholderTextColor={colors.muted}
            value={username}
            onChangeText={setUsername}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="Passwort"
            secureTextEntry
            textContentType={registration ? 'newPassword' : 'password'}
            placeholder="Passwort"
            placeholderTextColor={colors.muted}
            value={password}
            onChangeText={setPassword}
            onSubmitEditing={submit}
            style={styles.input}
          />
          {registration && <>
            <TextInput accessibilityLabel="Passwort wiederholen" secureTextEntry textContentType="newPassword" placeholder="Passwort wiederholen" value={confirmation} onChangeText={setConfirmation} style={styles.input} />
            <TextInput accessibilityLabel="Einladungscode oder Link" autoCapitalize="none" autoCorrect={false} placeholder="Einladungscode oder Link (optional)" value={invitationToken} onChangeText={setInvitationToken} style={styles.input} />
            <Text style={styles.subtitle}>Mindestens 10 Zeichen. Jeder meldet sich mit eigenem Passwort an.</Text>
          </>}
          <PrimaryButton label={registration ? 'Zur Anmeldung' : 'Konto erstellen'} onPress={() => { setRegistration(!registration); setError(''); setPassword(''); setConfirmation(''); }} disabled={busy} />
          <PrimaryButton label="Als Gast ansehen" disabled={busy} onPress={() => { setBusy(true); setError(''); void signInAsGuest(server).catch(reason => setError(reason instanceof Error ? reason.message : 'Gastzugang fehlgeschlagen.')).finally(() => setBusy(false)); }} />
          {providers.map(provider => <PrimaryButton key={provider.id} label={`Mit ${provider.name} fortfahren`} disabled={busy} onPress={() => {
            setBusy(true); setError(''); setPassword(''); setConfirmation('');
            void (async () => {
              const invite = invitationToken.trim();
              const invitation = registration && invite ? (invite.includes('://') ? new URL(invite).searchParams.get('invite') || '' : invite) : '';
              if (registration && invite && !invitation) throw new Error('Im Link fehlt der Einladungscode.');
              await signInWithProvider(server, provider.id, invitation);
            })().catch(reason => setError(reason instanceof Error ? reason.message : 'Anmeldung fehlgeschlagen.')).finally(() => setBusy(false));
          }} />)}
          {providersError && <PrimaryButton label="Weitere Anmeldemöglichkeiten erneut laden" disabled={busy} onPress={() => setProvidersRetry(value => value + 1)} />}
          {!!sessionWarning && (
            <View style={styles.warningBox}>
              <Text accessibilityRole="alert" style={styles.warning}>{sessionWarning}</Text>
              {authCleanupPending && (
                <Pressable
                  accessibilityRole="button"
                  onPress={() => void retryAuthCleanup()}
                  style={styles.cleanupButton}>
                  <Text style={styles.cleanupButtonText}>Schlüsselbund erneut bereinigen</Text>
                </Pressable>
              )}
            </View>
          )}
          {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          <PrimaryButton
            label={busy ? (registration ? 'Konto wird erstellt …' : 'Anmelden …') : (registration ? 'Konto erstellen' : 'Anmelden')}
            onPress={submit}
            disabled={busy || !server.trim() || !username.trim() || !password || (registration && (!confirmation || password.length < 10))}
          />
          <Text style={styles.privacy}>Dein Passwort wird nicht gespeichert. Die Sitzung liegt im iOS-Schlüsselbund.</Text>
          <Pressable accessibilityRole="link" onPress={() => void openPrivacy()} style={styles.privacyLinkButton}>
            <Text style={styles.privacyLink}>Datenschutzhinweise ansehen</Text>
          </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.butter },
  keyboard: { flex: 1 },
  container: { flexGrow: 1, justifyContent: 'center', padding: space.lg, gap: space.lg },
  mark: {
    width: 62,
    height: 62,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.text,
  },
  markText: { color: colors.butter, fontSize: 34, fontWeight: '900' },
  intro: { gap: 6 },
  title: { color: colors.text, fontSize: 42, letterSpacing: -1.2, fontWeight: '900' },
  subtitle: { color: colors.text, fontSize: 17, lineHeight: 24, maxWidth: 330 },
  form: {
    padding: space.md,
    borderRadius: radii.lg,
    backgroundColor: colors.cream,
    gap: 12,
  },
  input: {
    minHeight: 52,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    color: colors.text,
    fontSize: 16,
  },
  warningBox: { gap: 8, padding: 12, borderRadius: radii.sm, backgroundColor: colors.warningSurface },
  warning: { color: colors.text, lineHeight: 20 },
  cleanupButton: { minHeight: 44, justifyContent: 'center' },
  cleanupButtonText: { color: colors.text, fontWeight: '900', textDecorationLine: 'underline' },
  error: { color: colors.danger, lineHeight: 20 },
  privacy: { color: colors.muted, textAlign: 'center', fontSize: 12, lineHeight: 17 },
  privacyLinkButton: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  privacyLink: { color: colors.text, fontSize: 13, fontWeight: '800', textDecorationLine: 'underline' },
});
