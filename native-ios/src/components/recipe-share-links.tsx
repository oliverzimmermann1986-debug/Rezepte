import React, { useCallback, useEffect, useState } from 'react';
import { Alert, FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';

type ShareLink = {
  id: string;
  created_at: number;
  expires_at: number;
  created_by?: string | null;
  revoked_at?: number | null;
  active: boolean | number;
};

function formatDate(value: number) {
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
    .format(new Date(value * 1000));
}

export function RecipeShareLinks({
  recipeId,
  visible,
  onClose,
}: {
  recipeId: number;
  visible: boolean;
  onClose: () => void;
}) {
  const [items, setItems] = useState<ShareLink[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await api<{ items: ShareLink[] }>(`/api/recipes/${recipeId}/shares`);
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Freigaben konnten nicht geladen werden');
    } finally {
      setLoading(false);
    }
  }, [recipeId]);

  useEffect(() => { if (visible) void load(); }, [load, visible]);

  function confirmRevoke(item: ShareLink) {
    Alert.alert(
      'Freigabe widerrufen?',
      'Der öffentliche Link funktioniert danach sofort nicht mehr.',
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Widerrufen', style: 'destructive', onPress: () => void revoke(item) },
      ],
    );
  }

  async function revoke(item: ShareLink) {
    setBusyId(item.id);
    setError('');
    try {
      await api(`/api/recipes/${recipeId}/shares/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Freigabe konnte nicht widerrufen werden');
    } finally {
      setBusyId('');
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable onPress={onClose} style={styles.headerAction}><Text style={styles.headerLink}>Fertig</Text></Pressable>
          <Text style={styles.title}>Öffentliche Freigaben</Text>
          <Pressable disabled={loading} onPress={() => void load()} style={styles.headerAction}><Text style={[styles.headerLink, styles.right, loading && styles.disabled]}>Laden</Text></Pressable>
        </View>
        <Text style={styles.help}>Jeder mit einem aktiven Link kann dieses einzelne Rezept ohne Anmeldung sehen. Ein Widerruf wirkt sofort.</Text>
        {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
        {loading && !items.length ? <StateView title="Freigaben werden geladen" loading /> : (
          <FlatList
            data={items}
            keyExtractor={item => item.id}
            contentContainerStyle={styles.content}
            renderItem={({ item }) => {
              const active = Boolean(item.active);
              return (
                <View style={[styles.item, active && styles.itemActive]}>
                  <View style={styles.itemText}>
                    <Text style={[styles.status, active ? styles.active : styles.inactive]}>{active ? 'Aktiv' : item.revoked_at ? 'Widerrufen' : 'Abgelaufen'}</Text>
                    <Text style={styles.meta}>Erstellt {formatDate(item.created_at)}{item.created_by ? ` von ${item.created_by}` : ''}</Text>
                    <Text style={styles.meta}>Gültig bis {formatDate(item.expires_at)}</Text>
                  </View>
                  {active && (
                    <Pressable disabled={!!busyId} onPress={() => confirmRevoke(item)} style={styles.revoke}>
                      <Text style={styles.revokeText}>{busyId === item.id ? '…' : 'Widerrufen'}</Text>
                    </Pressable>
                  )}
                </View>
              );
            }}
            ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
            ListEmptyComponent={<StateView title="Keine Freigaben" message="Über „Teilen“ kannst du einen auf sieben Tage begrenzten Link erstellen." />}
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
  help: { margin: space.md, marginBottom: 0, color: colors.muted, fontSize: 13, lineHeight: 19 },
  error: { margin: space.md, marginBottom: 0, padding: 12, color: colors.danger, backgroundColor: colors.dangerSurface, borderRadius: radii.sm },
  content: { padding: space.md, paddingBottom: 60 },
  item: { minHeight: 88, padding: 13, flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  itemActive: { borderColor: colors.success },
  itemText: { flex: 1, gap: 4 },
  status: { fontSize: 14, fontWeight: '900' },
  active: { color: colors.success },
  inactive: { color: colors.muted },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  revoke: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 },
  revokeText: { color: colors.danger, fontWeight: '800' },
});
