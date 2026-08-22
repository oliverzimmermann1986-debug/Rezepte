import React, { useCallback, useEffect, useState } from 'react';
import { Alert, FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { RecipeListItem } from '@/lib/types';

type TrashItem = RecipeListItem & {
  deleted_at: number;
  days_until_purge: number;
  files_deleted?: boolean;
};

function formatDate(value: number) {
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(value * 1000));
}

export function AdminTrash({
  visible,
  onClose,
  onChanged,
}: {
  visible: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<TrashItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await api<{ items: TrashItem[] }>('/api/recipes/trash/list?limit=200');
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Papierkorb konnte nicht geladen werden');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (visible) void load(); }, [load, visible]);

  async function restore(item: TrashItem) {
    setBusyId(item.id);
    setError('');
    try {
      await api(`/api/recipes/${item.id}/restore`, { method: 'POST' });
      await load();
      onChanged();
      Alert.alert('Wiederhergestellt', `„${item.name}“ ist wieder bei den Rezepten.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Wiederherstellung fehlgeschlagen');
    } finally {
      setBusyId(null);
    }
  }

  function confirmPurge(item: TrashItem) {
    Alert.alert(
      'Endgültig löschen?',
      `„${item.name}“ und die zugehörigen Dateien können danach nicht wiederhergestellt werden.`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Endgültig löschen', style: 'destructive', onPress: () => void purge(item) },
      ],
    );
  }

  async function purge(item: TrashItem) {
    setBusyId(item.id);
    setError('');
    try {
      await api(`/api/recipes/${item.id}?delete_files=true&hard=true`, { method: 'DELETE' });
      setItems(current => current.filter(value => value.id !== item.id));
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Endgültiges Löschen fehlgeschlagen');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable onPress={onClose} style={styles.headerAction}><Text style={styles.headerLink}>Fertig</Text></Pressable>
          <Text style={styles.title}>Papierkorb</Text>
          <Pressable onPress={() => void load()} disabled={loading} style={styles.headerAction}><Text style={[styles.headerLink, styles.right, loading && styles.disabled]}>Laden</Text></Pressable>
        </View>
        {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
        {loading && !items.length ? <StateView title="Papierkorb wird geladen" loading /> : (
          <FlatList
            data={items}
            keyExtractor={item => String(item.id)}
            contentContainerStyle={styles.content}
            renderItem={({ item }) => (
              <View style={styles.item}>
                <View style={styles.itemText}>
                  <Text style={styles.recipe}>{item.name}</Text>
                  <Text style={styles.meta}>{[item.type, item.category].filter(Boolean).join(' · ')}</Text>
                  <Text style={styles.meta}>Gelöscht {formatDate(item.deleted_at)} · noch {Math.ceil(item.days_until_purge)} Tage</Text>
                  {!!item.files_deleted && <Text style={styles.warning}>Die Originaldateien wurden bereits entfernt.</Text>}
                </View>
                <View style={styles.actions}>
                  <Pressable disabled={busyId !== null} onPress={() => void restore(item)} style={styles.restore}>
                    <Text style={styles.restoreText}>{busyId === item.id ? '…' : 'Wiederherstellen'}</Text>
                  </Pressable>
                  <Pressable disabled={busyId !== null} onPress={() => confirmPurge(item)} style={styles.delete}>
                    <Text style={styles.deleteText}>Löschen</Text>
                  </Pressable>
                </View>
              </View>
            )}
            ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
            ListEmptyComponent={<StateView title="Papierkorb ist leer" message="Gelöschte Rezepte bleiben hier bis zu 30 Tage wiederherstellbar." />}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { minWidth: 70, minHeight: 44, justifyContent: 'center' },
  headerLink: { color: colors.text, fontSize: 15, fontWeight: '800' },
  right: { textAlign: 'right' },
  disabled: { opacity: 0.4 },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  error: { margin: space.md, marginBottom: 0, padding: 12, color: colors.danger, backgroundColor: colors.dangerSurface, borderRadius: radii.sm },
  content: { padding: space.md, paddingBottom: 60 },
  item: { padding: 13, gap: 12, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  itemText: { gap: 4 },
  recipe: { color: colors.text, fontSize: 17, fontWeight: '900' },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  warning: { color: colors.warning, fontSize: 12, fontWeight: '700' },
  actions: { flexDirection: 'row', gap: 8 },
  restore: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm, backgroundColor: colors.butter },
  restoreText: { color: colors.text, fontWeight: '900' },
  delete: { minWidth: 88, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  deleteText: { color: colors.danger, fontWeight: '800' },
});
