import { SymbolView } from 'expo-symbols';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton, StateView, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { invalidateApiCacheByPrefix } from '@/lib/cache';

type FindingType = 'category_mismatch' | 'name_mismatch' | 'folder_mismatch';

type AiFinding = {
  id: number;
  recipe_id: number;
  recipe_name: string;
  finding_type: FindingType;
  current_value: string;
  suggested_value: string;
  reason: string;
};

type AiSortStatus = {
  running: boolean;
  total: number;
  processed: number;
  findings: number;
  error: string | null;
};

type AiSortSnapshot = {
  items: AiFinding[];
  counts: Record<FindingType, number>;
  total_open: number;
  eligible_recipes: number;
  status: AiSortStatus;
};

const findingLabels: Record<FindingType, string> = {
  category_mismatch: 'Typ & Kategorie',
  name_mismatch: 'Rezeptname',
  folder_mismatch: 'Ordnername',
};

export function AdminAiSort({
  visible,
  onClose,
  onChanged,
}: {
  visible: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [snapshot, setSnapshot] = useState<AiSortSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();

    const load = async () => {
      setLoading(true);
      try {
        const next = await api<AiSortSnapshot>(
          '/api/audit/ai-sanity/findings',
          {},
          controller.signal,
        );
        if (!active) return;
        setSnapshot(next);
        setError(next.status.error || '');
        if (next.status.running) timer = setTimeout(() => void load(), 2000);
      } catch (reason) {
        if (active && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'KI-Sortierung konnte nicht geladen werden.');
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    void load();
    return () => {
      active = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [reloadKey, visible]);

  const groupedCounts = useMemo(() => (
    snapshot?.counts || { category_mismatch: 0, name_mismatch: 0, folder_mismatch: 0 }
  ), [snapshot]);

  function requestStart() {
    const count = snapshot?.eligible_recipes || 0;
    Alert.alert(
      'Speisekarte mit KI prüfen?',
      `${count} Rezepte mit ausreichend Beschreibung werden geprüft. Die KI erstellt nur Vorschläge; geändert wird erst nach deiner Bestätigung. Dabei können Kosten beim konfigurierten KI-Anbieter entstehen.`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Prüfung starten', onPress: () => void startSort() },
      ],
    );
  }

  async function startSort() {
    setStarting(true);
    setError('');
    try {
      const result = await api<{ ok: boolean; total: number }>('/api/audit/ai-sanity', {
        method: 'POST',
      });
      setSnapshot(current => current ? {
        ...current,
        status: { running: true, total: result.total, processed: 0, findings: 0, error: null },
      } : current);
      setReloadKey(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'KI-Prüfung konnte nicht gestartet werden.');
    } finally {
      setStarting(false);
    }
  }

  function requestApply(finding: AiFinding) {
    const moveWarning = finding.finding_type === 'category_mismatch'
      ? '\n\nDas Rezept wird dabei in die neue Kategorie verschoben.'
      : finding.finding_type === 'folder_mismatch'
        ? '\n\nDer Rezeptordner wird dabei umbenannt.'
        : '\n\nName, Ordner und Metadaten werden gemeinsam aktualisiert.';
    Alert.alert(
      'KI-Vorschlag übernehmen?',
      `„${finding.current_value}“\n→ „${finding.suggested_value}“${moveWarning}`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Übernehmen', onPress: () => void applyFinding(finding) },
      ],
    );
  }

  async function applyFinding(finding: AiFinding) {
    setBusyId(finding.id);
    setError('');
    try {
      await api(`/api/audit/finding/${finding.id}/apply`, { method: 'POST' });
      await invalidateApiCacheByPrefix('recipe:', 'recipes:');
      onChanged();
      setReloadKey(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Vorschlag konnte nicht übernommen werden.');
    } finally {
      setBusyId(null);
    }
  }

  async function ignoreFinding(finding: AiFinding) {
    setBusyId(finding.id);
    setError('');
    try {
      await api(`/api/audit/finding/${finding.id}/resolve`, { method: 'POST' });
      setReloadKey(value => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Vorschlag konnte nicht verworfen werden.');
    } finally {
      setBusyId(null);
    }
  }

  const running = Boolean(snapshot?.status.running);
  const close = () => {
    if (busyId === null && !starting) onClose();
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={close}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable accessibilityRole="button" disabled={Boolean(busyId) || starting} onPress={close} style={styles.headerAction}>
            <Text style={styles.headerActionText}>Fertig</Text>
          </Pressable>
          <Text style={styles.title}>KI-Sortierung</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="KI-Sortierung aktualisieren" onPress={() => setReloadKey(value => value + 1)} style={styles.headerActionRight}>
            <SymbolView name="arrow.clockwise" size={20} weight="semibold" tintColor={colors.text} />
          </Pressable>
        </View>

        {loading && !snapshot ? (
          <StateView title="KI-Sortierung wird geladen" loading />
        ) : (
          <FlatList
            data={running ? [] : (snapshot?.items || [])}
            keyExtractor={item => String(item.id)}
            contentContainerStyle={styles.content}
            ListHeaderComponent={(
              <>
                <View style={sharedStyles.card}>
                  <Text style={sharedStyles.sectionTitle}>Speisekarte prüfen</Text>
                  <Text style={styles.help}>
                    Die KI prüft Typ, Kategorie, Rezeptname und Ordner anhand der Beschreibung. Änderungen werden nie automatisch übernommen.
                  </Text>
                  {running ? (
                    <View accessible accessibilityLiveRegion="polite" style={styles.progress}>
                      <ActivityIndicator color={colors.text} />
                      <View style={styles.progressCopy}>
                        <Text style={styles.progressTitle}>KI-Prüfung läuft</Text>
                        <Text style={styles.progressText}>{snapshot?.status.processed || 0} von {snapshot?.status.total || 0} Rezepten · {snapshot?.status.findings || 0} Vorschläge</Text>
                      </View>
                    </View>
                  ) : (
                    <PrimaryButton
                      label={starting ? 'KI-Prüfung startet …' : snapshot?.total_open ? 'Speisekarte erneut prüfen' : 'KI-Sortierung starten'}
                      onPress={requestStart}
                      disabled={starting || !snapshot?.eligible_recipes}
                    />
                  )}
                  <Text style={styles.eligible}>{snapshot?.eligible_recipes || 0} Rezepte können geprüft werden.</Text>
                </View>

                {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}

                <View style={styles.summaryRow}>
                  <Summary value={groupedCounts.category_mismatch} label="Kategorie" />
                  <Summary value={groupedCounts.name_mismatch} label="Name" />
                  <Summary value={groupedCounts.folder_mismatch} label="Ordner" />
                </View>

                <View style={styles.findingsHeader}>
                  <Text style={sharedStyles.sectionTitle}>Vorschläge</Text>
                  <Text style={styles.total}>{snapshot?.total_open || 0} offen</Text>
                </View>
              </>
            )}
            ListEmptyComponent={(
              <Text style={running ? styles.pendingResults : styles.empty}>
                {running ? 'Neue Vorschläge erscheinen nach Abschluss der Prüfung.' : 'Keine offenen KI-Vorschläge.'}
              </Text>
            )}
            renderItem={({ item: finding }) => (
              <View style={styles.finding}>
                <View style={styles.findingTop}>
                  <Text style={styles.kind}>{findingLabels[finding.finding_type]}</Text>
                  <Text style={styles.recipeId}>ID {finding.recipe_id}</Text>
                </View>
                <Text style={styles.recipeName}>{finding.recipe_name}</Text>
                <View style={styles.change}>
                  <Text style={styles.current}>{finding.current_value || '–'}</Text>
                  <Text style={styles.arrow}>↓</Text>
                  <Text style={styles.suggestion}>{finding.suggested_value}</Text>
                </View>
                {!!finding.reason && <Text style={styles.reason}>{finding.reason}</Text>}
                <View style={styles.actions}>
                  <Pressable
                    accessibilityRole="button"
                    disabled={busyId !== null}
                    onPress={() => void ignoreFinding(finding)}
                    style={({ pressed }) => [styles.ignoreButton, pressed && styles.pressed, busyId !== null && styles.disabled]}>
                    <Text style={styles.ignoreLabel}>Ignorieren</Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    disabled={busyId !== null}
                    onPress={() => requestApply(finding)}
                    style={({ pressed }) => [styles.applyButton, pressed && styles.pressed, busyId !== null && styles.disabled]}>
                    {busyId === finding.id ? <ActivityIndicator color={colors.text} /> : <Text style={styles.applyLabel}>Übernehmen</Text>}
                  </Pressable>
                </View>
              </View>
            )}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

function Summary({ value, label }: { value: number; label: string }) {
  return (
    <View style={styles.summary}>
      <Text style={styles.summaryValue}>{value}</Text>
      <Text style={styles.summaryLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { width: 74, minHeight: 44, justifyContent: 'center' },
  headerActionRight: { width: 74, minHeight: 44, alignItems: 'flex-end', justifyContent: 'center' },
  headerActionText: { color: colors.text, fontSize: 15, fontWeight: '800' },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  content: { padding: space.md, paddingBottom: 48, gap: space.md },
  help: { color: colors.muted, lineHeight: 20 },
  eligible: { color: colors.muted, fontSize: 12 },
  progress: { minHeight: 58, padding: 12, flexDirection: 'row', alignItems: 'center', gap: 12, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  progressCopy: { flex: 1, gap: 3 },
  progressTitle: { color: colors.text, fontWeight: '900' },
  progressText: { color: colors.muted, fontSize: 13 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
  summaryRow: { flexDirection: 'row', gap: 8 },
  summary: { flex: 1, minHeight: 76, padding: 10, justifyContent: 'space-between', borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  summaryValue: { color: colors.text, fontSize: 24, fontWeight: '900' },
  summaryLabel: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  findingsHeader: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  total: { color: colors.muted, fontWeight: '700' },
  pendingResults: { color: colors.muted, paddingVertical: 18 },
  empty: { color: colors.success, paddingVertical: 18 },
  finding: { padding: 14, gap: 9, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  findingTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  kind: { color: colors.warning, fontSize: 12, fontWeight: '900', letterSpacing: 0.5, textTransform: 'uppercase' },
  recipeId: { color: colors.muted, fontSize: 12 },
  recipeName: { color: colors.text, fontSize: 17, fontWeight: '900' },
  change: { padding: 11, gap: 4, borderRadius: radii.sm, backgroundColor: colors.white },
  current: { color: colors.muted, textDecorationLine: 'line-through' },
  arrow: { color: colors.muted, fontSize: 12 },
  suggestion: { color: colors.text, fontSize: 16, fontWeight: '900' },
  reason: { color: colors.muted, lineHeight: 19 },
  actions: { flexDirection: 'row', gap: 8 },
  ignoreButton: { flex: 1, minHeight: 46, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.white },
  ignoreLabel: { color: colors.muted, fontWeight: '800' },
  applyButton: { flex: 1.35, minHeight: 46, alignItems: 'center', justifyContent: 'center', borderRadius: radii.md, backgroundColor: colors.butter },
  applyLabel: { color: colors.text, fontWeight: '900' },
  pressed: { opacity: 0.76, transform: [{ scale: 0.98 }] },
  disabled: { opacity: 0.45 },
});
