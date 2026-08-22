import React, { useState } from 'react';
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

export default function LoginScreen() {
  const {
    serverUrl: storedServer,
    cloudflareClientId: storedCloudflareClientId,
    cloudflareClientSecret: storedCloudflareClientSecret,
    signIn,
  } = useAuth();
  const [server, setServer] = useState(storedServer);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [cloudflareClientId, setCloudflareClientId] = useState(storedCloudflareClientId);
  const [cloudflareClientSecret, setCloudflareClientSecret] = useState(storedCloudflareClientSecret);
  const [showCloudflare, setShowCloudflare] = useState(
    Boolean(storedCloudflareClientId || storedCloudflareClientSecret),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    setBusy(true);
    setError('');
    try {
      await signIn(server, username, password, cloudflareClientId, cloudflareClientSecret);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Verbindung zum Server fehlgeschlagen.');
    } finally {
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
            <Text style={styles.title}>Rezepte</Text>
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
            textContentType="password"
            placeholder="Passwort"
            placeholderTextColor={colors.muted}
            value={password}
            onChangeText={setPassword}
            onSubmitEditing={submit}
            style={styles.input}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: showCloudflare }}
            onPress={() => setShowCloudflare((value) => !value)}
            style={styles.cloudflareToggle}>
            <Text style={styles.cloudflareToggleText}>🛡 Cloudflare-Gerätezugang</Text>
            <Text style={styles.cloudflareChevron}>{showCloudflare ? '−' : '+'}</Text>
          </Pressable>
          {showCloudflare && (
            <View style={styles.cloudflarePanel}>
              <TextInput
                accessibilityLabel="Cloudflare Client-ID"
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="Cloudflare Client-ID"
                placeholderTextColor={colors.muted}
                value={cloudflareClientId}
                onChangeText={setCloudflareClientId}
                style={styles.input}
              />
              <TextInput
                accessibilityLabel="Cloudflare Client-Secret"
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
                placeholder="Cloudflare Client-Secret"
                placeholderTextColor={colors.muted}
                value={cloudflareClientSecret}
                onChangeText={setCloudflareClientSecret}
                style={styles.input}
              />
              <Text style={styles.cloudflareHint}>
                Beide Werte werden im iOS-Schlüsselbund gespeichert und bei jeder Serveranfrage gesendet.
              </Text>
            </View>
          )}
          {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          <PrimaryButton
            label={busy ? 'Anmelden …' : 'Anmelden'}
            onPress={submit}
            disabled={busy || !server.trim() || !username.trim() || !password}
          />
          <Text style={styles.privacy}>Passwort wird nicht gespeichert. Sitzung und Gerätezugang liegen im iOS-Schlüsselbund.</Text>
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
  cloudflareToggle: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 4,
  },
  cloudflareToggleText: { color: colors.text, fontSize: 15, fontWeight: '700' },
  cloudflareChevron: { color: colors.text, fontSize: 24, lineHeight: 28 },
  cloudflarePanel: { gap: 10 },
  cloudflareHint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  error: { color: colors.danger, lineHeight: 20 },
  privacy: { color: colors.muted, textAlign: 'center', fontSize: 12, lineHeight: 17 },
  privacyLinkButton: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  privacyLink: { color: colors.text, fontSize: 13, fontWeight: '800', textDecorationLine: 'underline' },
});
