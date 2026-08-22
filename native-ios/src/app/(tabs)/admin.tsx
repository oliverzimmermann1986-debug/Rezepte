import React, { useCallback, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { Alert, Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import * as DocumentPicker from 'expo-document-picker';

import { PendingEditor } from '@/components/pending-editor';
import { PrimaryButton, Screen, StateView, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api, deleteCachedFile, uploadFile } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { pickEditedJpeg } from '@/lib/image-picker';
import { FailedDownload, PendingItem } from '@/lib/types';

type Overview = {
  counts: {
    recipes: number;
    pending: number;
    failed_downloads: number;
    open_findings: number;
    versions: number;
    trash: number;
  };
  pdf_count: number;
  db_size_bytes: number;
};

export default function AdminScreen() {
  const { username, serverUrl, sessionWarning, signOut } = useAuth();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [pending, setPending] = useState<PendingItem[]>([]);
  const [failed, setFailed] = useState<FailedDownload[]>([]);
  const [selectedPending, setSelectedPending] = useState<PendingItem | null>(null);
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [overviewResult, pendingResult, failedResult] = await Promise.allSettled([
        api<Overview>('/api/admin/overview'),
        api<PendingItem[]>('/api/pending?status=pending&sort=newest'),
        api<FailedDownload[]>('/api/pending/failed'),
      ]);
      if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value);
      if (pendingResult.status === 'fulfilled') setPending(pendingResult.value);
      if (failedResult.status === 'fulfilled') setFailed(failedResult.value);
      const rejected = [overviewResult, pendingResult, failedResult]
        .find(result => result.status === 'rejected');
      if (rejected?.status === 'rejected') {
        setError(rejected.reason instanceof Error ? rejected.reason.message : 'Ein Bereich konnte nicht geladen werden');
      }
    } catch {
      setError('Admin-Daten konnten nicht geladen werden');
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => {
    void load();
  }, [load]));

  async function importUrl() {
    const source = url.trim();
    if (!source) return;
    setBusy(true);
    try {
      const result = await api<{ ok: boolean; status?: string; message?: string }>('/api/pending/import-url', {
        method: 'POST',
        body: JSON.stringify({ url: source, type: 'recipe' }),
      });
      setUrl('');
      Alert.alert(
        result.ok ? 'Import übernommen' : 'Import fehlgeschlagen',
        result.message || (result.status === 'pending' ? 'Zur manuellen Prüfung vorgemerkt.' : 'Rezept wurde verarbeitet.'),
      );
      await load();
    } catch (reason) {
      Alert.alert('Import fehlgeschlagen', reason instanceof Error ? reason.message : 'Unbekannter Fehler');
    } finally {
      setBusy(false);
    }
  }

  async function runImporter() {
    setBusy(true);
    try {
      await api('/api/jobs/scraper/run', { method: 'POST' });
      Alert.alert('Import gestartet', 'Neue Quellen werden im Hintergrund verarbeitet.');
    } catch (reason) {
      Alert.alert('Import nicht gestartet', reason instanceof Error ? reason.message : 'Unbekannter Fehler');
    } finally {
      setBusy(false);
    }
  }

  async function uploadSelected(file: { uri: string; name: string; mimeType: string }) {
    setBusy(true);
    try {
      const result = await uploadFile<{ ok: boolean; status?: string; message?: string }>(
        '/api/pending/import-file?type=recipe',
        file,
      );
      Alert.alert(
        result.status === 'pending' ? 'Zur Prüfung vorgemerkt' : 'Datei importiert',
        result.message || 'Die Datei wurde verarbeitet.',
      );
      await load();
    } catch (reason) {
      Alert.alert('Upload fehlgeschlagen', reason instanceof Error ? reason.message : 'Datei konnte nicht hochgeladen werden');
    } finally {
      await deleteCachedFile(file.uri).catch(() => undefined);
      setBusy(false);
    }
  }

  async function pickImage() {
    try {
      const image = await pickEditedJpeg('rezept-import');
      if (image) await uploadSelected(image);
    } catch (reason) {
      Alert.alert('Bild nicht verfügbar', reason instanceof Error ? reason.message : 'Auswahl fehlgeschlagen');
    }
  }

  async function pickPdf() {
    const result = await DocumentPicker.getDocumentAsync({
      type: 'application/pdf',
      copyToCacheDirectory: true,
      multiple: false,
    });
    if (result.canceled) return;
    const asset = result.assets[0];
    await uploadSelected({ uri: asset.uri, name: asset.name, mimeType: asset.mimeType || 'application/pdf' });
  }

  async function failedAction(item: FailedDownload, action: 'retry' | 'discard') {
    setBusy(true);
    try {
      await api(`/api/pending/failed/${action}`, {
        method: 'POST',
        body: JSON.stringify({ url: item.url }),
      });
      await load();
    } catch (reason) {
      Alert.alert('Aktion fehlgeschlagen', reason instanceof Error ? reason.message : 'Unbekannter Fehler');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen topSafe contentStyle={styles.content}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>SYSTEM & PFLEGE</Text>
          <Text style={styles.title}>Administration</Text>
        </View>
        <Pressable onPress={() => load()} style={styles.refresh}><Text style={styles.refreshText}>↻</Text></Pressable>
      </View>

      {!!sessionWarning && <Text accessibilityRole="alert" style={styles.warning}>{sessionWarning}</Text>}

      {loading && !overview && !pending.length && !failed.length ? (
        <StateView title="Status wird geladen" loading />
      ) : (
        <>
          {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          <View style={styles.kpis}>
            <Kpi value={overview?.counts.recipes || 0} label="Rezepte" />
            <Kpi value={overview?.counts.pending || 0} label="Offene Importe" warning />
            <Kpi value={overview?.counts.failed_downloads || 0} label="Fehlgeschlagen" warning />
            <Kpi value={overview?.pdf_count || 0} label="PDFs" />
          </View>

          <View style={sharedStyles.card}>
            <Text style={sharedStyles.sectionTitle}>Direktimport</Text>
            <Text style={styles.help}>TikTok-/Instagram-Link, Foto oder lokales PDF übernehmen. Social-Medien bleiben bei der Plattform; gespeichert wird nur der Link.</Text>
            <TextInput
              accessibilityLabel="TikTok- oder Instagram-Link"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="https://www.tiktok.com/…"
              placeholderTextColor={colors.muted}
              value={url}
              onChangeText={setUrl}
              style={[sharedStyles.input, styles.urlInput]}
            />
            <PrimaryButton label={busy ? 'Import läuft …' : 'Link importieren'} onPress={importUrl} disabled={busy || !url.trim()} />
            <View style={styles.uploadRow}>
              <View style={styles.uploadButton}><PrimaryButton label="Bild auswählen" onPress={pickImage} disabled={busy} /></View>
              <View style={styles.uploadButton}><PrimaryButton label="PDF auswählen" onPress={pickPdf} disabled={busy} /></View>
            </View>
            <PrimaryButton label="Postfächer jetzt prüfen" onPress={runImporter} disabled={busy} />
          </View>

          <View style={styles.section}>
            <Text style={sharedStyles.sectionTitle}>Manuelle Prüfung</Text>
            {!pending.length ? <Text style={styles.empty}>Keine offenen Importe.</Text> : pending.map(item => (
              <Pressable key={item.url} onPress={() => setSelectedPending(item)} style={styles.pending}>
                <View style={styles.pendingText}>
                  <Text style={styles.pendingTitle} numberOfLines={1}>{item.ai_suggestion?.name || item.ai_suggestion?.filename || 'Unvollständiger Import'}</Text>
                  <Text style={styles.pendingUrl} numberOfLines={2}>{item.url}</Text>
                  <Text style={styles.pendingMeta}>Antippen zum Bearbeiten{item.ai_suggestion?.confidence != null ? ` · ${Math.round(item.ai_suggestion.confidence * 100)} % erkannt` : ''}</Text>
                </View>
                <Text style={styles.chevron}>›</Text>
              </Pressable>
            ))}
          </View>

          {!!failed.length && (
            <View style={styles.section}>
              <Text style={sharedStyles.sectionTitle}>Alte fehlgeschlagene Importe</Text>
              {failed.map(item => (
                <View key={item.url} style={styles.failed}>
                  <Text style={styles.pendingTitle} numberOfLines={1}>Download fehlgeschlagen · {item.attempts} Versuche</Text>
                  <Text style={styles.pendingUrl} numberOfLines={2}>{item.url}</Text>
                  {!!item.last_error && <Text style={styles.failedError} numberOfLines={2}>{item.last_error}</Text>}
                  <View style={styles.failedActions}>
                    <Pressable onPress={() => failedAction(item, 'retry')} disabled={busy}><Text style={styles.retry}>Erneut versuchen</Text></Pressable>
                    <Pressable onPress={() => failedAction(item, 'discard')} disabled={busy}><Text style={styles.skip}>Verwerfen</Text></Pressable>
                  </View>
                </View>
              ))}
            </View>
          )}

          <View style={sharedStyles.card}>
            <Text style={sharedStyles.sectionTitle}>Konto</Text>
            <Text style={styles.account}>Angemeldet als {username || 'lokal'}</Text>
            <Text style={styles.server} numberOfLines={2}>{serverUrl}</Text>
            <PrimaryButton label="Datenschutz" onPress={() => Linking.openURL(`${serverUrl}/privacy`)} />
            <PrimaryButton label="Abmelden" onPress={signOut} destructive />
          </View>
        </>
      )}
      <PendingEditor
        item={selectedPending}
        onClose={() => setSelectedPending(null)}
        onSaved={async () => {
          setSelectedPending(null);
          await load();
        }}
      />
    </Screen>
  );
}

function Kpi({ value, label, warning }: { value: number; label: string; warning?: boolean }) {
  return (
    <View style={[styles.kpi, warning && value > 0 && styles.kpiWarning]}>
      <Text style={styles.kpiValue}>{value}</Text>
      <Text style={styles.kpiLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { gap: space.md, paddingBottom: 120 },
  header: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', paddingTop: 4 },
  eyebrow: { color: colors.muted, fontSize: 11, letterSpacing: 1.5, fontWeight: '800' },
  title: { color: colors.text, fontSize: 32, letterSpacing: -0.8, fontWeight: '900' },
  refresh: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center' },
  refreshText: { color: colors.text, fontSize: 27 },
  kpis: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  kpi: {
    width: '48%',
    minHeight: 100,
    padding: 14,
    justifyContent: 'space-between',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  kpiWarning: { backgroundColor: colors.warningSurface, borderColor: '#D69A48' },
  kpiValue: { color: colors.text, fontSize: 29, fontWeight: '900' },
  kpiLabel: { color: colors.muted, fontSize: 13, fontWeight: '700' },
  help: { color: colors.muted, lineHeight: 20 },
  urlInput: { fontSize: 15 },
  uploadRow: { flexDirection: 'row', gap: 8 },
  uploadButton: { flex: 1 },
  section: { gap: 10 },
  empty: { color: colors.success, paddingVertical: 12 },
  pending: {
    minHeight: 74,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  pendingText: { flex: 1, gap: 4 },
  pendingTitle: { color: colors.text, fontWeight: '800' },
  pendingUrl: { color: colors.muted, fontSize: 12 },
  pendingMeta: { color: colors.success, fontSize: 12, fontWeight: '700' },
  chevron: { color: colors.muted, fontSize: 28 },
  failed: { padding: 12, gap: 5, borderWidth: 1, borderColor: '#D69A48', borderRadius: radii.md, backgroundColor: colors.warningSurface },
  failedError: { color: colors.warning, fontSize: 12 },
  failedActions: { flexDirection: 'row', justifyContent: 'space-between', paddingTop: 5 },
  retry: { color: colors.success, minHeight: 36, paddingTop: 8, fontWeight: '800' },
  skip: { color: colors.danger, minHeight: 40, paddingTop: 11, fontWeight: '700' },
  account: { color: colors.text, fontSize: 16, fontWeight: '700' },
  server: { color: colors.muted, fontSize: 13 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: '#FCE8E5' },
  warning: { color: colors.text, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.warningSurface },
});
