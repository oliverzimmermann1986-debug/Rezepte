import React, { useCallback, useEffect, useState } from 'react';
import { Alert, FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { invalidateApiCacheByPrefix } from '@/lib/cache';

type VersionItem = {
  id: number;
  recipe_id: number;
  recipe_name?: string | null;
  version_no: number;
  created_at: number;
  created_by?: string | null;
  source?: string | null;
  reason?: string | null;
};

type VersionDetail = VersionItem & {
  diff: {
    fields: { field: string; before: unknown; current: unknown }[];
    ingredients_added_since: string[];
    ingredients_removed_since: string[];
    steps_changed: boolean;
    before_counts: { ingredients: number; steps: number; tags: number };
  };
};

function formatDate(value: number) {
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(value * 1000));
}

function readable(value: unknown) {
  if (value == null || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Ja' : 'Nein';
  return String(value);
}

export function AdminVersions({
  visible,
  onClose,
  onChanged,
}: {
  visible: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<VersionItem[]>([]);
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await api<{ items: VersionItem[] }>('/api/admin/versions?limit=200');
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Versionen konnten nicht geladen werden');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) void load();
    else setDetail(null);
  }, [load, visible]);

  async function openDetail(item: VersionItem) {
    setBusyId(item.id);
    setError('');
    try {
      setDetail(await api<VersionDetail>(`/api/admin/versions/${item.id}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Version konnte nicht geladen werden');
    } finally {
      setBusyId(null);
    }
  }

  function confirmRestore(item: VersionItem) {
    Alert.alert(
      `Version v${item.version_no} wiederherstellen?`,
      `Der aktuelle Stand von „${item.recipe_name || `Rezept #${item.recipe_id}`}“ wird zuvor als neue Version gesichert.`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Wiederherstellen', onPress: () => void restore(item) },
      ],
    );
  }

  async function restore(item: VersionItem) {
    setBusyId(item.id);
    setError('');
    try {
      await api(`/api/admin/versions/${item.id}/restore`, { method: 'POST' });
      await invalidateApiCacheByPrefix('recipe:', 'recipes:');
      setDetail(null);
      await load();
      onChanged();
      Alert.alert('Wiederhergestellt', 'Der frühere Rezeptstand ist wieder aktiv.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Wiederherstellung fehlgeschlagen');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable onPress={detail ? () => setDetail(null) : onClose} style={styles.headerAction}>
            <Text style={styles.headerLink}>{detail ? 'Zurück' : 'Fertig'}</Text>
          </Pressable>
          <Text style={styles.title}>{detail ? `Version v${detail.version_no}` : 'Versionen'}</Text>
          <Pressable onPress={() => void load()} disabled={loading || !!detail} style={styles.headerAction}>
            <Text style={[styles.headerLink, (loading || !!detail) && styles.disabled]}>Laden</Text>
          </Pressable>
        </View>
        {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
        {detail ? (
          <FlatList
            data={detail.diff.fields}
            keyExtractor={item => item.field}
            contentContainerStyle={styles.content}
            ListHeaderComponent={
              <View style={styles.detailHeader}>
                <Text style={styles.recipe}>{detail.recipe_name || `Rezept #${detail.recipe_id}`}</Text>
                <Text style={styles.meta}>{detail.reason || 'Änderung'} · {formatDate(detail.created_at)}</Text>
                <View style={styles.counts}>
                  <Count value={detail.diff.before_counts.ingredients} label="Zutaten" />
                  <Count value={detail.diff.before_counts.steps} label="Schritte" />
                  <Count value={detail.diff.before_counts.tags} label="Tags" />
                </View>
                {detail.diff.steps_changed && <Text style={styles.change}>Zubereitungsschritte wurden verändert.</Text>}
                {!!detail.diff.ingredients_added_since.length && <Text style={styles.change}>Hinzugekommen: {detail.diff.ingredients_added_since.join(', ')}</Text>}
                {!!detail.diff.ingredients_removed_since.length && <Text style={styles.change}>Entfernt: {detail.diff.ingredients_removed_since.join(', ')}</Text>}
              </View>
            }
            renderItem={({ item }) => (
              <View style={styles.fieldChange}>
                <Text style={styles.field}>{item.field}</Text>
                <Text style={styles.before}>{readable(item.before)}</Text>
                <Text style={styles.arrow}>→</Text>
                <Text style={styles.current}>{readable(item.current)}</Text>
              </View>
            )}
            ListEmptyComponent={<Text style={styles.empty}>Der aktuelle Stand entspricht den gespeicherten Feldern dieser Version.</Text>}
            ListFooterComponent={
              <Pressable disabled={busyId !== null} onPress={() => confirmRestore(detail)} style={styles.restoreButton}>
                <Text style={styles.restoreText}>{busyId === detail.id ? 'Wird wiederhergestellt …' : 'Diese Version wiederherstellen'}</Text>
              </Pressable>
            }
          />
        ) : loading && !items.length ? (
          <StateView title="Versionen werden geladen" loading />
        ) : (
          <FlatList
            data={items}
            keyExtractor={item => String(item.id)}
            contentContainerStyle={styles.content}
            renderItem={({ item }) => (
              <Pressable disabled={busyId !== null} onPress={() => void openDetail(item)} style={styles.item}>
                <View style={styles.itemText}>
                  <Text style={styles.recipe}>{item.recipe_name || `Rezept #${item.recipe_id}`}</Text>
                  <Text style={styles.reason}>v{item.version_no} · {item.reason || 'Änderung'}</Text>
                  <Text style={styles.meta}>{formatDate(item.created_at)} · {item.created_by || 'system'}</Text>
                </View>
                <Text style={styles.chevron}>{busyId === item.id ? '…' : '›'}</Text>
              </Pressable>
            )}
            ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
            ListEmptyComponent={<StateView title="Noch keine Versionen" message="Vor der nächsten Rezeptänderung wird automatisch ein Stand gesichert." />}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

function Count({ value, label }: { value: number; label: string }) {
  return <View style={styles.count}><Text style={styles.countValue}>{value}</Text><Text style={styles.countLabel}>{label}</Text></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { minWidth: 70, minHeight: 44, justifyContent: 'center' },
  headerLink: { color: colors.text, fontSize: 15, fontWeight: '800' },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  disabled: { opacity: 0.4, textAlign: 'right' },
  error: { margin: space.md, marginBottom: 0, padding: 12, color: colors.danger, backgroundColor: colors.dangerSurface, borderRadius: radii.sm },
  content: { padding: space.md, paddingBottom: 60 },
  item: { minHeight: 82, padding: 13, flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  itemText: { flex: 1, gap: 4 },
  recipe: { color: colors.text, fontSize: 17, fontWeight: '900' },
  reason: { color: colors.text, fontSize: 14, fontWeight: '700' },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  chevron: { color: colors.muted, fontSize: 28 },
  detailHeader: { gap: 10, paddingBottom: space.md },
  counts: { flexDirection: 'row', gap: 8 },
  count: { flex: 1, padding: 12, borderRadius: radii.md, backgroundColor: colors.surface },
  countValue: { color: colors.text, fontSize: 22, fontWeight: '900' },
  countLabel: { color: colors.muted, fontSize: 12 },
  change: { color: colors.text, lineHeight: 20, padding: 10, borderRadius: radii.sm, backgroundColor: colors.warningSurface },
  fieldChange: { paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  field: { color: colors.text, fontWeight: '900', marginBottom: 5 },
  before: { color: colors.muted },
  arrow: { color: colors.muted, paddingVertical: 2 },
  current: { color: colors.text, fontWeight: '700' },
  empty: { color: colors.muted, paddingVertical: space.lg, textAlign: 'center' },
  restoreButton: { minHeight: 52, marginTop: space.lg, alignItems: 'center', justifyContent: 'center', borderRadius: radii.md, backgroundColor: colors.butter },
  restoreText: { color: colors.text, fontWeight: '900' },
});
