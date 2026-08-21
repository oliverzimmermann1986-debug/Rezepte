import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton, StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { CartItem } from '@/lib/types';

export default function CartScreen() {
  const [items, setItems] = useState<CartItem[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const mutating = useRef(new Set<number>());

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const result = await api<{ items: CartItem[] }>('/api/cart');
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Einkaufsliste konnte nicht geladen werden');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function addItem() {
    const next = name.trim();
    if (!next) return;
    try {
      await api('/api/cart/add', { method: 'POST', body: JSON.stringify({ name: next }) });
      setName('');
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Artikel konnte nicht hinzugefügt werden');
    }
  }

  async function toggle(item: CartItem) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    const nextChecked = !item.checked;
    setItems(current => current.map(value => value.id === item.id ? { ...value, checked: !value.checked } : value));
    try {
      await api(`/api/cart/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ checked: nextChecked }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Änderung konnte nicht gespeichert werden');
      await load();
    } finally {
      mutating.current.delete(item.id);
    }
  }

  async function remove(item: CartItem) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    try {
      await api(`/api/cart/${item.id}`, { method: 'DELETE' });
      setItems(current => current.filter(value => value.id !== item.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Artikel konnte nicht entfernt werden');
    } finally {
      mutating.current.delete(item.id);
    }
  }

  function clearChecked() {
    Alert.alert('Erledigte entfernen?', 'Abgehakte Einträge werden aus der Liste gelöscht.', [
      { text: 'Abbrechen', style: 'cancel' },
      {
        text: 'Entfernen',
        style: 'destructive',
        onPress: async () => {
          await api('/api/cart/clear', { method: 'POST', body: JSON.stringify({ only_checked: true }) });
          await load();
        },
      },
    ]);
  }

  const openCount = items.filter(item => !item.checked).length;

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>GEMEINSAME LISTE</Text>
          <Text style={styles.title}>Einkauf</Text>
        </View>
        <Text style={styles.count}>{openCount} offen</Text>
      </View>
      <View style={styles.addRow}>
        <TextInput
          accessibilityLabel="Artikel hinzufügen"
          placeholder="Artikel hinzufügen"
          placeholderTextColor={colors.muted}
          value={name}
          onChangeText={setName}
          onSubmitEditing={addItem}
          returnKeyType="done"
          style={styles.input}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Hinzufügen"
          onPress={addItem}
          disabled={!name.trim()}
          style={({ pressed }) => [styles.addButton, pressed && styles.pressed, !name.trim() && styles.disabled]}>
          <Text style={styles.addText}>+</Text>
        </Pressable>
      </View>
      {!!error && !!items.length && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
      {loading && !items.length ? (
        <StateView title="Einkaufsliste wird geladen" loading />
      ) : error && !items.length ? (
        <StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => load()} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={item => String(item.id)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={colors.text} />}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <View style={styles.item}>
              <Pressable
                accessibilityRole="checkbox"
                accessibilityState={{ checked: item.checked }}
                onPress={() => toggle(item)}
                style={[styles.check, item.checked && styles.checkDone]}>
                <Text style={styles.checkText}>{item.checked ? '✓' : ''}</Text>
              </Pressable>
              <Pressable style={styles.itemText} onPress={() => toggle(item)}>
                <Text style={[styles.name, item.checked && styles.nameDone]}>{item.name}</Text>
                <Text style={styles.amount}>
                  {item.amount == null ? '' : `${item.amount} `}{item.unit || ''}
                </Text>
              </Pressable>
              <Pressable accessibilityLabel={`${item.name} entfernen`} onPress={() => remove(item)} hitSlop={10}>
                <Text style={styles.remove}>×</Text>
              </Pressable>
            </View>
          )}
          ItemSeparatorComponent={() => <View style={styles.separator} />}
          ListEmptyComponent={<StateView title="Alles eingekauft" message="Die Liste ist leer." />}
          ListFooterComponent={items.some(item => item.checked)
            ? <View style={styles.footer}><PrimaryButton label="Erledigte entfernen" onPress={clearChecked} destructive /></View>
            : null}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: {
    paddingHorizontal: space.md,
    paddingTop: 10,
    paddingBottom: 12,
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
  },
  eyebrow: { color: colors.muted, fontSize: 11, letterSpacing: 1.5, fontWeight: '800' },
  title: { color: colors.text, fontSize: 36, letterSpacing: -1, fontWeight: '900' },
  count: { color: colors.text, paddingBottom: 5, fontWeight: '800' },
  addRow: { paddingHorizontal: space.md, paddingBottom: 12, flexDirection: 'row', gap: 8 },
  input: {
    flex: 1,
    minHeight: 50,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    color: colors.text,
    fontSize: 16,
  },
  addButton: {
    width: 50,
    height: 50,
    borderRadius: radii.md,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.butter,
  },
  addText: { color: colors.text, fontSize: 28, fontWeight: '700' },
  pressed: { opacity: 0.75, transform: [{ scale: 0.97 }] },
  disabled: { opacity: 0.4 },
  error: { color: colors.danger, paddingHorizontal: space.md, paddingBottom: 8 },
  list: { paddingHorizontal: space.md, paddingBottom: 120 },
  item: { minHeight: 62, flexDirection: 'row', alignItems: 'center', gap: 12 },
  check: {
    width: 32,
    height: 32,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.white,
  },
  checkDone: { borderColor: colors.success, backgroundColor: colors.success },
  checkText: { color: colors.white, fontWeight: '900' },
  itemText: { flex: 1, minHeight: 48, justifyContent: 'center' },
  name: { color: colors.text, fontSize: 17, fontWeight: '600' },
  nameDone: { color: colors.muted, textDecorationLine: 'line-through' },
  amount: { color: colors.muted, marginTop: 2 },
  remove: { color: colors.muted, fontSize: 26, minWidth: 36, textAlign: 'center' },
  separator: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border, marginLeft: 44 },
  footer: { paddingVertical: space.lg },
});
