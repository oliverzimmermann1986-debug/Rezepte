import React, { useState } from 'react';
import {
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
import { api, ApiError } from '@/lib/api';
import { invalidateApiCache } from '@/lib/cache';

type OptimizedItem = {
  name: string;
  amount?: number | null;
  unit?: string | null;
  checked: boolean;
  category: string;
  source_item_ids: number[];
};

type OptimizationPreview = {
  preview_id: string;
  items: OptimizedItem[];
  summary: {
    original_count: number;
    optimized_count: number;
    merged_count: number;
    renamed_count: number;
    categorized_count: number;
  };
  expires_in_seconds: number;
};

export function ShoppingAiOptimizer({
  visible,
  onClose,
  onApplied,
}: {
  visible: boolean;
  onClose: () => void;
  onApplied: () => void | Promise<void>;
}) {
  const [preview, setPreview] = useState<OptimizationPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');

  function close() {
    if (loading || applying) return;
    setPreview(null);
    setError('');
    onClose();
  }

  async function createPreview() {
    setLoading(true);
    setError('');
    try {
      const result = await api<OptimizationPreview>('/api/cart/optimize/preview', {
        method: 'POST',
      });
      setPreview(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'KI-Vorschau konnte nicht erstellt werden.');
    } finally {
      setLoading(false);
    }
  }

  function requestApply() {
    if (!preview) return;
    Alert.alert(
      'Optimierte Liste übernehmen?',
      `${preview.summary.optimized_count} Einträge ersetzen die aktuelle Liste. Mengen, Einheiten, Häkchen und Rezeptquellen bleiben erhalten.`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Übernehmen', onPress: () => void applyPreview() },
      ],
    );
  }

  async function applyPreview() {
    if (!preview) return;
    setApplying(true);
    setError('');
    try {
      await api('/api/cart/optimize/apply', {
        method: 'POST',
        body: JSON.stringify({ preview_id: preview.preview_id }),
      });
      await invalidateApiCache('cart');
      await onApplied();
      Alert.alert('Einkaufsliste optimiert', 'Die geprüfte Sortierung wurde übernommen.');
      closeAfterApply();
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 409 || reason.status === 410)) {
        setPreview(null);
      }
      setError(reason instanceof Error ? reason.message : 'Optimierung konnte nicht übernommen werden.');
    } finally {
      setApplying(false);
    }
  }

  function closeAfterApply() {
    setPreview(null);
    setError('');
    onClose();
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={close}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable accessibilityRole="button" disabled={loading || applying} onPress={close} style={styles.headerAction}>
            <Text style={styles.headerActionText}>Abbrechen</Text>
          </Pressable>
          <Text style={styles.title}>KI optimieren</Text>
          <View style={styles.headerAction} />
        </View>

        {loading ? (
          <StateView title="KI sortiert die Einkaufsliste" message="Mengen und Einheiten bleiben auf dem Server." loading />
        ) : preview ? (
          <FlatList
            data={preview.items}
            keyExtractor={item => item.source_item_ids.join('-')}
            contentContainerStyle={styles.content}
            ListHeaderComponent={(
              <>
                <View style={sharedStyles.card}>
                  <Text style={sharedStyles.sectionTitle}>Vorschau</Text>
                  <Text style={styles.help}>Prüfe die Liste vor dem Übernehmen. An die KI wurden ausschließlich die Artikelnamen gesendet.</Text>
                  <View style={styles.summaryRow}>
                    <Summary value={preview.summary.optimized_count} label="Einträge" />
                    <Summary value={preview.summary.merged_count} label="Zusammengeführt" />
                    <Summary value={preview.summary.renamed_count} label="Umbenannt" />
                  </View>
                </View>
                {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
              </>
            )}
            renderItem={({ item }) => (
              <View style={[styles.item, item.checked && styles.itemChecked]}>
                <View style={styles.itemText}>
                  <Text style={[styles.itemName, item.checked && styles.checkedText]}>{item.name}</Text>
                  <Text style={styles.itemMeta}>{formatAmount(item)}{formatAmount(item) ? ' · ' : ''}{item.category}</Text>
                </View>
                {item.source_item_ids.length > 1 && (
                  <Text style={styles.mergeBadge}>{item.source_item_ids.length}×</Text>
                )}
              </View>
            )}
            ItemSeparatorComponent={() => <View style={styles.separator} />}
            ListFooterComponent={(
              <View style={styles.footer}>
                <PrimaryButton label={applying ? 'Wird übernommen …' : 'Optimierung übernehmen'} onPress={requestApply} disabled={applying} />
                <Text style={styles.expiry}>Die Vorschau ist 15 Minuten gültig und verfällt bei jeder Listenänderung.</Text>
              </View>
            )}
          />
        ) : (
          <View style={styles.startContent}>
            <View style={sharedStyles.card}>
              <Text style={sharedStyles.sectionTitle}>Einkauf einfacher ablaufen</Text>
              <Text style={styles.help}>Die KI vereinheitlicht Artikelnamen, erkennt sichere Dubletten und sortiert nach Einkaufsbereichen.</Text>
              <View style={styles.guardrail}>
                <Text style={styles.guardrailTitle}>Mengen bleiben geschützt</Text>
                <Text style={styles.guardrailText}>Mengen, Einheiten, Häkchen und Rezeptzuordnungen werden nicht von der KI verändert.</Text>
              </View>
              <PrimaryButton label="Vorschau erstellen" onPress={createPreview} />
            </View>
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          </View>
        )}
      </SafeAreaView>
    </Modal>
  );
}

function formatAmount(item: OptimizedItem) {
  if (item.amount == null) return item.unit || '';
  return `${String(item.amount).replace('.', ',')} ${item.unit || ''}`.trim();
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
  headerAction: { width: 86, minHeight: 44, justifyContent: 'center' },
  headerActionText: { color: colors.text, fontSize: 15, fontWeight: '800' },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  startContent: { padding: space.md, gap: space.md },
  content: { padding: space.md, paddingBottom: 48, gap: space.sm },
  help: { color: colors.muted, lineHeight: 20 },
  guardrail: { padding: 12, gap: 3, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  guardrailTitle: { color: colors.text, fontWeight: '900' },
  guardrailText: { color: colors.muted, lineHeight: 19 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
  summaryRow: { flexDirection: 'row', gap: 8 },
  summary: { flex: 1, minHeight: 70, padding: 9, justifyContent: 'space-between', borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, backgroundColor: colors.white },
  summaryValue: { color: colors.text, fontSize: 22, fontWeight: '900' },
  summaryLabel: { color: colors.muted, fontSize: 11, fontWeight: '700' },
  item: { minHeight: 68, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: radii.md, backgroundColor: colors.surface },
  itemChecked: { opacity: 0.58 },
  itemText: { flex: 1, gap: 4 },
  itemName: { color: colors.text, fontSize: 16, fontWeight: '800' },
  checkedText: { textDecorationLine: 'line-through' },
  itemMeta: { color: colors.muted, fontSize: 13 },
  mergeBadge: { minWidth: 32, paddingHorizontal: 8, paddingVertical: 5, overflow: 'hidden', borderRadius: 12, backgroundColor: colors.butter, color: colors.text, textAlign: 'center', fontWeight: '900' },
  separator: { height: 7 },
  footer: { paddingTop: space.md, gap: space.sm },
  expiry: { color: colors.muted, fontSize: 12, lineHeight: 17, textAlign: 'center' },
});
