import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  SectionList,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton, StateView } from '@/components/ui';
import { ShoppingAiOptimizer } from '@/components/shopping-ai-optimizer';
import { UnitPicker } from '@/components/unit-picker';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { apiCached, invalidateApiCache } from '@/lib/cache';
import { isValidDateInput, localDateInput } from '@/lib/date-input';
import { CartItem, RecurringCartItem } from '@/lib/types';
import { normalizeUnit } from '@/lib/units';

type RecurringForm = {
  id: number | null;
  name: string;
  amount: string;
  unit: string;
  category: string;
  interval: string;
  nextDueOn: string;
  active: boolean;
};

type CartSection = {
  title: string;
  data: CartItem[];
  openCount: number;
};

const SHOPPING_CATEGORIES = [
  'Obst & Gemüse',
  'Bäckerei',
  'Fleisch & Fisch',
  'Kühlregal',
  'Vorrat & Konserven',
  'Getränke',
  'Tiefkühl',
  'Drogerie & Haushalt',
  'Sonstiges',
] as const;

const CATEGORY_ORDER = new Map<string, number>(
  SHOPPING_CATEGORIES.map((category, index) => [category, index]),
);

const amountFormatter = new Intl.NumberFormat('de-DE', {
  maximumFractionDigits: 2,
});

const emptyRecurringForm = (): RecurringForm => ({
  id: null,
  name: '',
  amount: '',
  unit: '',
  category: '',
  interval: '7',
  nextDueOn: localDateInput(),
  active: true,
});

export default function CartScreen() {
  const { width, fontScale } = useWindowDimensions();
  const [tab, setTab] = useState<'list' | 'recurring'>('list');
  const [items, setItems] = useState<CartItem[]>([]);
  const [recurring, setRecurring] = useState<RecurringCartItem[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState<RecurringForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [showAiOptimizer, setShowAiOptimizer] = useState(false);
  const mutating = useRef(new Set<number>());

  const loadCart = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const result = await apiCached<{ items: CartItem[] }>('cart', '/api/cart');
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Einkaufsliste konnte nicht geladen werden');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const loadRecurring = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const result = await apiCached<{ items: RecurringCartItem[] }>('recurring-cart', '/api/cart/recurring');
      setRecurring(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Wiederkehrende Einkäufe konnten nicht geladen werden');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => {
    if (tab === 'list') void loadCart();
    else void loadRecurring();
  }, [loadCart, loadRecurring, tab]));

  function selectTab(next: 'list' | 'recurring') {
    setTab(next);
    setError('');
  }

  async function addItem() {
    const next = name.trim();
    if (!next) return;
    try {
      await api('/api/cart/add', { method: 'POST', body: JSON.stringify({ name: next }) });
      await invalidateApiCache('cart');
      setName('');
      await loadCart();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Artikel konnte nicht hinzugefügt werden');
    }
  }

  async function toggle(item: CartItem) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    const nextChecked = !item.checked;
    setItems(current => current.map(value => value.id === item.id ? { ...value, checked: nextChecked } : value));
    try {
      await api(`/api/cart/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ checked: nextChecked }),
      });
      await invalidateApiCache('cart');
      await loadCart();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Änderung konnte nicht gespeichert werden');
      await loadCart();
    } finally {
      mutating.current.delete(item.id);
    }
  }

  async function remove(item: CartItem) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    try {
      await api(`/api/cart/${item.id}`, { method: 'DELETE' });
      await invalidateApiCache('cart');
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
          try {
            await api('/api/cart/clear', { method: 'POST', body: JSON.stringify({ only_checked: true }) });
            await invalidateApiCache('cart');
            await loadCart();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Erledigte Artikel konnten nicht entfernt werden');
          }
        },
      },
    ]);
  }

  function editRecurring(item: RecurringCartItem) {
    setEditor({
      id: item.id,
      name: item.name,
      amount: item.amount == null ? '' : String(item.amount).replace('.', ','),
      unit: normalizeUnit(item.default_unit),
      category: item.category || '',
      interval: String(item.interval_days),
      nextDueOn: item.next_due_on,
      active: item.active,
    });
  }

  async function saveRecurring() {
    if (!editor || saving) return;
    const interval = Number(editor.interval);
    const parsedAmount = editor.amount.trim() ? Number(editor.amount.trim().replace(',', '.')) : null;
    if (!editor.name.trim()) {
      Alert.alert('Artikel fehlt', 'Bitte einen Artikelnamen eintragen.');
      return;
    }
    if (!Number.isInteger(interval) || interval < 1 || interval > 3650) {
      Alert.alert('Intervall ungültig', 'Bitte 1 bis 3650 ganze Tage eintragen.');
      return;
    }
    if (parsedAmount !== null && (!Number.isFinite(parsedAmount) || parsedAmount <= 0)) {
      Alert.alert('Menge ungültig', 'Die Menge muss größer als 0 sein.');
      return;
    }
    if (!isValidDateInput(editor.nextDueOn)) {
      Alert.alert('Datum ungültig', 'Bitte ein echtes Kalenderdatum als JJJJ-MM-TT eintragen.');
      return;
    }
    setSaving(true);
    try {
      const body = JSON.stringify({
        name: editor.name.trim(),
        amount: parsedAmount,
        default_unit: normalizeUnit(editor.unit) || null,
        category: editor.category.trim() || null,
        interval_days: interval,
        next_due_on: editor.nextDueOn,
        active: editor.active,
      });
      await api(editor.id ? `/api/cart/recurring/${editor.id}` : '/api/cart/recurring', {
        method: editor.id ? 'PATCH' : 'POST',
        body,
      });
      await invalidateApiCache('recurring-cart');
      setEditor(null);
      await loadRecurring();
    } catch (reason) {
      Alert.alert('Speichern fehlgeschlagen', reason instanceof Error ? reason.message : 'Bitte erneut versuchen.');
    } finally {
      setSaving(false);
    }
  }

  async function setRecurringActive(item: RecurringCartItem, active: boolean) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    setRecurring(current => current.map(value => value.id === item.id ? { ...value, active } : value));
    try {
      await api(`/api/cart/recurring/${item.id}`, { method: 'PATCH', body: JSON.stringify({ active }) });
      await invalidateApiCache('recurring-cart');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Status konnte nicht gespeichert werden');
      await loadRecurring();
    } finally {
      mutating.current.delete(item.id);
    }
  }

  function deleteRecurring(item: RecurringCartItem) {
    Alert.alert(`„${item.name}“ löschen?`, 'Die Wiederholung wird dauerhaft entfernt.', [
      { text: 'Abbrechen', style: 'cancel' },
      {
        text: 'Löschen',
        style: 'destructive',
        onPress: async () => {
          try {
            await api(`/api/cart/recurring/${item.id}`, { method: 'DELETE' });
            await invalidateApiCache('recurring-cart');
            await loadRecurring();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Wiederholung konnte nicht gelöscht werden');
          }
        },
      },
    ]);
  }

  async function runDue() {
    if (running) return;
    setRunning(true);
    try {
      const result = await api<{ count: number }>('/api/cart/recurring/run', {
        method: 'POST', body: JSON.stringify({}),
      });
      await invalidateApiCache('cart', 'recurring-cart');
      Alert.alert(
        result.count ? 'Zur Liste hinzugefügt' : 'Nichts fällig',
        result.count === 1 ? 'Ein Artikel wurde eingetragen.' : `${result.count} Artikel wurden eingetragen.`,
      );
      await loadRecurring();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Fällige Artikel konnten nicht eingetragen werden');
    } finally {
      setRunning(false);
    }
  }

  const openCount = items.filter(item => !item.checked).length;
  const dueCount = recurring.filter(item => item.active && item.due_in_days <= 0).length;
  const cartSections = useMemo<CartSection[]>(() => {
    const grouped = new Map<string, CartItem[]>();
    for (const item of items) {
      const category = item.category?.trim() || 'Sonstiges';
      const group = grouped.get(category) || [];
      group.push(item);
      grouped.set(category, group);
    }

    return Array.from(grouped, ([title, data]) => ({
      title,
      data: [...data].sort((left, right) => Number(left.checked) - Number(right.checked)),
      openCount: data.filter(item => !item.checked).length,
    })).sort((left, right) => {
      const rankDifference = categoryRank(left.title) - categoryRank(right.title);
      return rankDifference || left.title.localeCompare(right.title, 'de');
    });
  }, [items]);

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <Text style={styles.title}>Einkauf</Text>
        <Text style={styles.count}>{tab === 'list' ? `${openCount} offen` : `${dueCount} fällig`}</Text>
      </View>
      <View style={styles.tabs} accessibilityRole="tablist">
        <TabButton label="Aktuelle Liste" selected={tab === 'list'} onPress={() => selectTab('list')} />
        <TabButton label="Wiederkehrend" selected={tab === 'recurring'} onPress={() => selectTab('recurring')} />
      </View>

      {tab === 'list' ? (
        <>
          <View style={styles.addRow}>
            <TextInput
              accessibilityLabel="Artikel hinzufügen"
              placeholder="Artikel hinzufügen"
              placeholderTextColor={colors.muted}
              value={name}
              onChangeText={setName}
              onSubmitEditing={addItem}
              returnKeyType="done"
              style={[styles.input, styles.addRowInput]}
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
          {!!items.length && (
            <Pressable
              accessibilityRole="button"
              onPress={() => setShowAiOptimizer(true)}
              style={({ pressed }) => [styles.aiButton, pressed && styles.pressed]}>
              <Text style={styles.aiButtonLabel}>Einkaufsliste mit KI optimieren</Text>
              <Text style={styles.aiButtonArrow}>›</Text>
            </Pressable>
          )}
          {!!error && !!items.length && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          {loading && !items.length ? (
            <StateView title="Einkaufsliste wird geladen" loading />
          ) : error && !items.length ? (
            <StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => loadCart()} />
          ) : (
            <SectionList
              sections={cartSections}
              keyExtractor={item => String(item.id)}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => loadCart(true)} tintColor={colors.text} />}
              contentContainerStyle={styles.list}
              stickySectionHeadersEnabled={false}
              renderSectionHeader={({ section }) => (
                <View style={styles.sectionHeader} accessibilityRole="header">
                  <Text style={styles.sectionTitle}>{section.title}</Text>
                  <Text style={styles.sectionCount}>
                    {section.openCount > 0 ? `${section.openCount} offen` : 'erledigt'}
                  </Text>
                </View>
              )}
              renderItem={({ item }) => (
                <View style={styles.item}>
                  <Pressable
                    accessibilityRole="checkbox"
                    accessibilityLabel={cartItemAccessibilityLabel(item)}
                    accessibilityState={{ checked: item.checked }}
                    style={styles.itemToggle}
                    onPress={() => toggle(item)}>
                    <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={[styles.check, item.checked && styles.checkDone]}>
                      <Text style={styles.checkText}>{item.checked ? '✓' : ''}</Text>
                    </View>
                    <View style={styles.itemText}>
                      <Text numberOfLines={2} style={[styles.name, item.checked && styles.nameDone]}>{item.name}</Text>
                    </View>
                    <View style={[styles.amountBadge, !formatCartAmount(item) && styles.amountBadgeEmpty]}>
                      <Text style={[styles.amountValue, item.checked && styles.amountDone]}>
                        {formatCartAmount(item) || '—'}
                      </Text>
                    </View>
                  </Pressable>
                  <Pressable accessibilityRole="button" accessibilityLabel={`${item.name} entfernen`} onPress={() => remove(item)} hitSlop={10}>
                    <Text style={styles.remove}>×</Text>
                  </Pressable>
                </View>
              )}
              ItemSeparatorComponent={() => <View style={styles.separator} />}
              ListEmptyComponent={<StateView title="Alles eingekauft" message="Die Liste ist leer." />}
              ListFooterComponent={items.some(item => item.checked) ? <View style={styles.footer}><PrimaryButton label="Erledigte entfernen" onPress={clearChecked} destructive /></View> : null}
            />
          )}
        </>
      ) : (
        <>
          <View style={styles.recurringActions}>
            <PrimaryButton label="Neue Wiederholung" onPress={() => setEditor(emptyRecurringForm())} />
            {dueCount > 0 && <PrimaryButton label={running ? 'Wird eingetragen …' : `${dueCount} fällige Artikel eintragen`} onPress={runDue} disabled={running} />}
          </View>
          {!!error && !!recurring.length && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
          {loading && !recurring.length ? (
            <StateView title="Wiederholungen werden geladen" loading />
          ) : error && !recurring.length ? (
            <StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => loadRecurring()} />
          ) : (
            <FlatList
              data={recurring}
              keyExtractor={item => String(item.id)}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => loadRecurring(true)} tintColor={colors.text} />}
              contentContainerStyle={styles.list}
              renderItem={({ item }) => (
                <View style={[styles.recurringItem, item.active && item.due_in_days <= 0 && styles.recurringDue]}>
                  <Pressable style={styles.recurringText} onPress={() => editRecurring(item)}>
                    <Text style={styles.name}>{item.name}</Text>
                    <Text style={styles.recurringMeta}>alle {item.interval_days} {item.interval_days === 1 ? 'Tag' : 'Tage'}{item.amount == null ? '' : ` · ${item.amount} ${item.default_unit || ''}`}</Text>
                    <Text style={[styles.dueText, !item.active && styles.inactiveText]}>{dueLabel(item)}</Text>
                  </Pressable>
                  <Switch accessibilityLabel={`${item.name} ${item.active ? 'pausieren' : 'aktivieren'}`} value={item.active} onValueChange={active => setRecurringActive(item, active)} trackColor={{ false: colors.border, true: colors.butter }} thumbColor={colors.white} />
                  <Pressable accessibilityLabel={`${item.name} bearbeiten`} onPress={() => editRecurring(item)} style={styles.editButton}><Text style={styles.editText}>✎</Text></Pressable>
                  <Pressable accessibilityLabel={`${item.name} Wiederholung löschen`} onPress={() => deleteRecurring(item)} hitSlop={8}><Text style={styles.remove}>×</Text></Pressable>
                </View>
              )}
              ItemSeparatorComponent={() => <View style={styles.separator} />}
              ListEmptyComponent={<StateView title="Noch keine Wiederholungen" message="Lege Artikel an, die regelmäßig auf deiner Einkaufsliste erscheinen sollen." />}
            />
          )}
        </>
      )}

      <Modal visible={!!editor} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setEditor(null)}>
        {editor && (
          <SafeAreaView style={styles.modalSafe} edges={['top', 'left', 'right', 'bottom']}>
            <KeyboardAvoidingView style={styles.modalBody} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
              <View style={styles.modalHeader}>
                <Pressable accessibilityRole="button" accessibilityLabel="Wiederholung schließen" onPress={() => setEditor(null)} hitSlop={10}><Text style={styles.modalLink}>Abbrechen</Text></Pressable>
                <Text style={styles.modalTitle}>{editor.id ? 'Wiederholung bearbeiten' : 'Neue Wiederholung'}</Text>
                <Pressable accessibilityRole="button" accessibilityLabel="Wiederholung speichern" onPress={saveRecurring} disabled={saving} hitSlop={10}><Text style={[styles.modalLink, styles.modalSave, saving && styles.disabled]}>{saving ? '…' : 'Speichern'}</Text></Pressable>
              </View>
              <ScrollView contentContainerStyle={styles.form} keyboardShouldPersistTaps="handled">
                <Field label="Artikel" value={editor.name} onChangeText={value => setEditor({ ...editor, name: value })} placeholder="z. B. Hafermilch" />
                <View style={[styles.formRow, (width < 390 || fontScale > 1.15) && styles.formColumn]}>
                  <View style={styles.formHalf}><Field label="Menge" value={editor.amount} onChangeText={value => setEditor({ ...editor, amount: value })} placeholder="z. B. 2" keyboardType="decimal-pad" /></View>
                  <View style={styles.formHalf}>
                    <View style={styles.field}>
                      <Text style={styles.fieldLabel}>Einheit</Text>
                      <UnitPicker value={editor.unit} onChange={unit => setEditor({ ...editor, unit })} />
                    </View>
                  </View>
                </View>
                <Field label="Kategorie (optional)" value={editor.category} onChangeText={value => setEditor({ ...editor, category: value })} placeholder="z. B. Kühlregal" />
                <Field label="Intervall in Tagen" value={editor.interval} onChangeText={value => setEditor({ ...editor, interval: value })} placeholder="7" keyboardType="number-pad" />
                <Field label="Nächster Termin (JJJJ-MM-TT)" value={editor.nextDueOn} onChangeText={value => setEditor({ ...editor, nextDueOn: value })} placeholder="2026-08-22" />
                <View style={styles.activeRow}>
                  <View><Text style={styles.fieldLabel}>Aktiv</Text><Text style={styles.helper}>Pausierte Regeln bleiben gespeichert.</Text></View>
                  <Switch accessibilityLabel="Wiederholung aktiv" value={editor.active} onValueChange={active => setEditor({ ...editor, active })} trackColor={{ false: colors.border, true: colors.butter }} />
                </View>
              </ScrollView>
            </KeyboardAvoidingView>
          </SafeAreaView>
        )}
      </Modal>
      <ShoppingAiOptimizer
        visible={showAiOptimizer}
        onClose={() => setShowAiOptimizer(false)}
        onApplied={() => loadCart()}
      />
    </SafeAreaView>
  );
}

function TabButton({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) {
  return <Pressable accessibilityRole="tab" accessibilityState={{ selected }} onPress={onPress} style={[styles.tab, selected && styles.tabActive]}><Text style={[styles.tabText, selected && styles.tabTextActive]}>{label}</Text></Pressable>;
}

function Field({ label, ...input }: { label: string } & React.ComponentProps<typeof TextInput>) {
  return <View style={styles.field}><Text style={styles.fieldLabel}>{label}</Text><TextInput {...input} placeholderTextColor={colors.muted} style={styles.input} /></View>;
}

function dueLabel(item: RecurringCartItem) {
  if (!item.active) return 'pausiert';
  if (item.due_in_days <= 0) return 'heute fällig';
  if (item.due_in_days === 1) return 'morgen fällig';
  return `in ${item.due_in_days} Tagen fällig`;
}

function formatCartAmount(item: Pick<CartItem, 'amount' | 'unit'>) {
  const unit = item.unit?.trim() || '';
  if (item.amount == null) return unit;
  return `${amountFormatter.format(item.amount)} ${unit}`.trim();
}

function categoryRank(category: string) {
  if (category === 'Sonstiges') return SHOPPING_CATEGORIES.length;
  return CATEGORY_ORDER.get(category) ?? SHOPPING_CATEGORIES.length - 1;
}

function cartItemAccessibilityLabel(item: CartItem) {
  const category = item.category?.trim() || 'Sonstiges';
  const amount = formatCartAmount(item) || 'ohne Mengenangabe';
  const action = item.checked ? 'wieder öffnen' : 'als erledigt markieren';
  return `${item.name}, ${amount}, ${category}: ${action}`;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { paddingHorizontal: space.md, paddingTop: 10, paddingBottom: 12, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
  title: { color: colors.text, fontSize: 36, letterSpacing: -1, fontWeight: '900' },
  count: { color: colors.text, paddingBottom: 5, fontWeight: '800' },
  tabs: { marginHorizontal: space.md, marginBottom: 14, flexDirection: 'row', borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 3, backgroundColor: colors.surface },
  tab: { flex: 1, minHeight: 44, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  tabActive: { backgroundColor: colors.butter },
  tabText: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  tabTextActive: { color: colors.text, fontWeight: '900' },
  addRow: { paddingHorizontal: space.md, paddingBottom: 12, flexDirection: 'row', gap: 8 },
  input: { minHeight: 50, paddingHorizontal: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.white, color: colors.text, fontSize: 16 },
  addRowInput: { flex: 1 },
  addButton: { width: 50, height: 50, borderRadius: radii.md, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.butter },
  addText: { color: colors.text, fontSize: 28, fontWeight: '700' },
  aiButton: { minHeight: 48, marginHorizontal: space.md, marginBottom: space.sm, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: colors.butterPressed, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  aiButtonLabel: { color: colors.text, fontWeight: '900' },
  aiButtonArrow: { color: colors.text, fontSize: 25, lineHeight: 28 },
  pressed: { opacity: 0.75, transform: [{ scale: 0.97 }] },
  disabled: { opacity: 0.4 },
  error: { color: colors.danger, paddingHorizontal: space.md, paddingBottom: 8 },
  list: { paddingHorizontal: space.md, paddingBottom: 120, flexGrow: 1 },
  sectionHeader: { minHeight: 42, paddingTop: 14, paddingBottom: 7, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', backgroundColor: colors.cream },
  sectionTitle: { flex: 1, color: colors.text, fontSize: 15, fontWeight: '900' },
  sectionCount: { color: colors.muted, fontSize: 12, fontWeight: '700', fontVariant: ['tabular-nums'] },
  item: { minHeight: 68, flexDirection: 'row', alignItems: 'center', gap: 8 },
  itemToggle: { flex: 1, minHeight: 68, flexDirection: 'row', alignItems: 'center', gap: 12 },
  check: { width: 32, height: 32, borderRadius: 11, borderWidth: 2, borderColor: colors.border, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.white },
  checkDone: { borderColor: colors.success, backgroundColor: colors.success },
  checkText: { color: colors.white, fontWeight: '900' },
  itemText: { flex: 1, minWidth: 0, minHeight: 48, justifyContent: 'center' },
  name: { color: colors.text, fontSize: 17, fontWeight: '700' },
  nameDone: { color: colors.muted, textDecorationLine: 'line-through' },
  amountBadge: { minWidth: 58, maxWidth: 108, minHeight: 34, paddingHorizontal: 10, paddingVertical: 7, justifyContent: 'center', borderRadius: radii.sm, backgroundColor: colors.warningSurface },
  amountBadgeEmpty: { backgroundColor: colors.surface },
  amountValue: { color: colors.text, fontSize: 14, fontWeight: '900', textAlign: 'right', fontVariant: ['tabular-nums'] },
  amountDone: { color: colors.muted },
  remove: { color: colors.muted, fontSize: 26, minWidth: 36, textAlign: 'center' },
  separator: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border, marginLeft: 44 },
  footer: { paddingVertical: space.lg },
  recurringActions: { paddingHorizontal: space.md, paddingBottom: space.sm, gap: space.sm },
  recurringItem: { minHeight: 82, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 12, borderRadius: radii.md },
  recurringDue: { borderWidth: 1, borderColor: colors.butterPressed, backgroundColor: '#FFF5CE' },
  recurringText: { flex: 1, minHeight: 62, justifyContent: 'center' },
  recurringMeta: { color: colors.muted, marginTop: 3 },
  dueText: { color: colors.warning, marginTop: 2, fontWeight: '700' },
  inactiveText: { color: colors.muted },
  editButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, backgroundColor: colors.surface },
  editText: { color: colors.text, fontSize: 22 },
  modalSafe: { flex: 1, backgroundColor: colors.cream },
  modalBody: { flex: 1 },
  modalHeader: { minHeight: 58, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  modalTitle: { color: colors.text, fontSize: 17, fontWeight: '800' },
  modalLink: { color: colors.text, fontSize: 16, minWidth: 72 },
  modalSave: { color: colors.warning, fontWeight: '900', textAlign: 'right' },
  form: { padding: space.md, gap: space.md },
  field: { gap: 6 },
  fieldLabel: { color: colors.text, fontSize: 14, fontWeight: '800' },
  formRow: { flexDirection: 'row', gap: space.sm },
  formColumn: { flexDirection: 'column' },
  formHalf: { flex: 1 },
  activeRow: { minHeight: 62, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  helper: { color: colors.muted, fontSize: 13, marginTop: 2 },
});
