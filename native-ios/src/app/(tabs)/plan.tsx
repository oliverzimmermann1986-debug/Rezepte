import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import * as Sharing from 'expo-sharing';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
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
import { api, deleteCachedFile, downloadFileToCache } from '@/lib/api';
import { apiCached } from '@/lib/cache';
import { MealPlan, MealPlanDay, MealPlanItem, RecipeListItem } from '@/lib/types';

const RECIPE_PAGE_SIZE = 60;

export default function PlanScreen() {
  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [weekStart, setWeekStart] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [pdfBusy, setPdfBusy] = useState(false);
  const [selectedDay, setSelectedDay] = useState<MealPlanDay | null>(null);
  const activeLoad = useRef<AbortController | null>(null);
  const loadGeneration = useRef(0);
  const mutating = useRef(new Set<number>());

  const load = useCallback(async (week = '', refresh = false) => {
    const generation = ++loadGeneration.current;
    activeLoad.current?.abort();
    const controller = new AbortController();
    activeLoad.current = controller;
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const suffix = week ? `?week_start=${encodeURIComponent(week)}` : '';
      const result = await apiCached<MealPlan>(
        `meal-plan:${week || 'current'}`,
        `/api/meal-plan${suffix}`,
        controller.signal,
      );
      if (generation !== loadGeneration.current) return;
      setPlan(result);
      setWeekStart(result.week_start);
    } catch (reason) {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : 'Wochenplan konnte nicht geladen werden');
    } finally {
      if (generation === loadGeneration.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => { load(''); }, [load]);
  useFocusEffect(useCallback(() => {
    if (weekStart) void load(weekStart);
    return () => activeLoad.current?.abort();
  }, [load, weekStart]));

  async function remove(item: MealPlanItem) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    try {
      await api(`/api/meal-plan/items/${item.id}`, { method: 'DELETE' });
      await load(plan?.week_start || weekStart);
    } catch (reason) {
      Alert.alert('Nicht entfernt', reason instanceof Error ? reason.message : 'Änderung fehlgeschlagen');
    } finally {
      mutating.current.delete(item.id);
    }
  }

  async function changeServings(item: MealPlanItem, delta: number) {
    if (mutating.current.has(item.id)) return;
    mutating.current.add(item.id);
    const next = Math.min(24, Math.max(1, item.planned_servings + delta));
    try {
      await api(`/api/meal-plan/items/${item.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ planned_servings: next }),
      });
      await load(plan?.week_start || weekStart);
    } catch (reason) {
      Alert.alert('Portionen nicht geändert', reason instanceof Error ? reason.message : 'Änderung fehlgeschlagen');
    } finally {
      mutating.current.delete(item.id);
    }
  }

  async function createCart() {
    if (!plan) return;
    try {
      const result = await api<{ added: number; merged: number }>('/api/meal-plan/cart', {
        method: 'POST',
        body: JSON.stringify({ week_start: plan.week_start }),
      });
      Alert.alert('Einkaufsliste ergänzt', `${result.added} neue und ${result.merged} vorhandene Artikel wurden übernommen.`);
    } catch (reason) {
      Alert.alert('Nicht möglich', reason instanceof Error ? reason.message : 'Erstellen fehlgeschlagen');
    }
  }

  async function sharePlanPdf() {
    if (!plan) return;
    setPdfBusy(true);
    let localUri = '';
    try {
      localUri = await downloadFileToCache(
        `/api/meal-plan/pdf?week_start=${encodeURIComponent(plan.week_start)}`,
        `wochenplan-${plan.week_start}.pdf`,
      );
      if (!await Sharing.isAvailableAsync()) throw new Error('Teilen ist auf diesem Gerät nicht verfügbar.');
      await Sharing.shareAsync(localUri, {
        mimeType: 'application/pdf',
        UTI: 'com.adobe.pdf',
        dialogTitle: `Wochenplan ab ${plan.week_start}`,
      });
    } catch (reason) {
      Alert.alert('PDF nicht geteilt', reason instanceof Error ? reason.message : 'Download fehlgeschlagen');
    } finally {
      if (localUri) await deleteCachedFile(localUri).catch(() => undefined);
      setPdfBusy(false);
    }
  }

  if (loading && !plan) {
    return <SafeAreaView style={styles.safe}><StateView title="Wochenplan wird geladen" loading /></SafeAreaView>;
  }
  if (error && !plan) {
    return <SafeAreaView style={styles.safe}><StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => load('')} /></SafeAreaView>;
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>ESSEN VORAUSPLANEN</Text>
          <Text style={styles.title}>Wochenplan</Text>
        </View>
        <Text style={styles.count}>{plan?.summary.planned_meals || 0} Gerichte</Text>
      </View>
      <View style={styles.weekNav}>
        <Pressable accessibilityRole="button" disabled={loading} onPress={() => plan && load(plan.previous_week)} style={styles.navButton}>
          <Text style={styles.navText}>‹ Vorherige</Text>
        </Pressable>
        <Text style={styles.weekText}>{plan?.week_start}</Text>
        <Pressable accessibilityRole="button" disabled={loading} onPress={() => plan && load(plan.next_week)} style={styles.navButton}>
          <Text style={styles.navText}>Nächste ›</Text>
        </Pressable>
      </View>
      {!plan?.is_current_week && (
        <Pressable accessibilityRole="button" disabled={loading} onPress={() => load('')} style={styles.todayButton}>
          <Text style={styles.todayText}>Zur aktuellen Woche</Text>
        </Pressable>
      )}
      <FlatList
        data={plan?.days || []}
        keyExtractor={day => day.date}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(weekStart, true)} tintColor={colors.text} />}
        contentContainerStyle={styles.list}
        renderItem={({ item: day }) => (
          <View style={[styles.day, day.is_today && styles.today]}>
            <View style={styles.dayHeader}>
              <View>
                <Text style={styles.dayLabel}>{day.label}</Text>
                <Text style={styles.dayDate}>{day.date}</Text>
              </View>
              <Pressable
                accessibilityLabel={`Rezept zu ${day.label} hinzufügen`}
                onPress={() => setSelectedDay(day)}
                style={styles.plus}>
                <Text style={styles.plusText}>+</Text>
              </Pressable>
            </View>
            {day.items.length ? day.items.map(item => (
              <View key={item.id} style={styles.meal}>
                <View style={styles.mealText}>
                  <Text style={styles.mealName}>{item.recipe_name}</Text>
                  <Text style={styles.servings}>{item.planned_servings} Portionen</Text>
                </View>
                <Pressable onPress={() => changeServings(item, -1)} style={styles.smallButton}><Text>−</Text></Pressable>
                <Pressable onPress={() => changeServings(item, 1)} style={styles.smallButton}><Text>+</Text></Pressable>
                <Pressable onPress={() => remove(item)} style={styles.smallButton}><Text style={styles.remove}>×</Text></Pressable>
              </View>
            )) : <Text style={styles.emptyDay}>Noch nichts geplant</Text>}
          </View>
        )}
        ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
        ListFooterComponent={
          <View style={styles.footer}>
            <Text style={styles.preview}>{plan?.summary.shopping_items || 0} Artikel für den Einkauf</Text>
            <PrimaryButton
              label="Wocheneinkauf erstellen"
              onPress={createCart}
              disabled={!plan?.summary.shopping_items}
            />
            <PrimaryButton
              label={pdfBusy ? 'PDF wird vorbereitet …' : 'Wochenplan als PDF teilen'}
              onPress={sharePlanPdf}
              disabled={pdfBusy || !plan?.summary.planned_meals}
            />
          </View>
        }
      />
      <RecipePicker
        day={selectedDay}
        onClose={() => setSelectedDay(null)}
        onAdded={async () => {
          setSelectedDay(null);
          await load(weekStart);
        }}
      />
    </SafeAreaView>
  );
}

function RecipePicker({
  day,
  onClose,
  onAdded,
}: {
  day: MealPlanDay | null;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [addingId, setAddingId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const activeRequest = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const recipesRef = useRef<RecipeListItem[]>([]);
  const loadingMoreRef = useRef(false);

  useEffect(() => { recipesRef.current = recipes; }, [recipes]);

  const loadRecipes = useCallback(async (search: string, append = false) => {
    if (append && loadingMoreRef.current) return;
    const generation = ++requestGeneration.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    if (append) {
      loadingMoreRef.current = true;
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    setError('');
    try {
      const params = new URLSearchParams({
        limit: String(RECIPE_PAGE_SIZE),
        offset: String(append ? recipesRef.current.length : 0),
        needs_manual_care: 'false',
      });
      if (search.trim()) params.set('search', search.trim());
      const result = await api<{ total: number; items: RecipeListItem[] }>(
        `/api/recipes?${params}`,
        {},
        controller.signal,
      );
      if (generation !== requestGeneration.current) return;
      setTotal(result.total);
      setRecipes(current => append ? [...current, ...result.items.filter(item => !current.some(known => known.id === item.id))] : result.items);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Rezepte konnten nicht geladen werden');
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false);
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!day) {
      activeRequest.current?.abort();
      setQuery('');
      setRecipes([]);
      setTotal(0);
      return;
    }
    const timer = setTimeout(() => void loadRecipes(query), 250);
    return () => {
      clearTimeout(timer);
      activeRequest.current?.abort();
    };
  }, [day, loadRecipes, query]);

  async function add(recipe: RecipeListItem) {
    if (!day) return;
    setAddingId(recipe.id);
    setError('');
    try {
      await api('/api/meal-plan/items', {
        method: 'POST',
        body: JSON.stringify({ planned_for: day.date, recipe_id: recipe.id, planned_servings: Math.min(24, Math.max(1, recipe.servings || 2)) }),
      });
      onAdded();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Rezept konnte nicht eingeplant werden');
    } finally {
      setAddingId(null);
    }
  }

  return (
    <Modal visible={!!day} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.picker}>
        <View style={styles.pickerHeader}>
          <Pressable onPress={onClose}><Text style={styles.navText}>Abbrechen</Text></Pressable>
          <Text style={styles.pickerTitle}>Rezept für {day?.label}</Text>
          <View style={{ width: 76 }} />
        </View>
        <TextInput
          placeholder="Kochfertige Rezepte suchen"
          placeholderTextColor={colors.muted}
          value={query}
          onChangeText={setQuery}
          style={styles.search}
        />
        {!!error && <Text accessibilityRole="alert" style={styles.pickerError}>{error}</Text>}
        {loading ? <StateView title="Rezepte werden geladen" loading /> : (
          <FlatList
            data={recipes}
            keyExtractor={item => String(item.id)}
            contentContainerStyle={styles.pickerList}
            renderItem={({ item }) => (
              <Pressable disabled={addingId !== null} onPress={() => add(item)} style={styles.pickerItem}>
                <Text style={styles.pickerName}>{item.name}</Text>
                {addingId === item.id ? <ActivityIndicator color={colors.text} /> : <Text style={styles.chevron}>›</Text>}
              </Pressable>
            )}
            ItemSeparatorComponent={() => <View style={styles.line} />}
            onEndReachedThreshold={0.4}
            onEndReached={() => {
              if (!loadingMore && recipes.length < total) void loadRecipes(query, true);
            }}
            ListFooterComponent={loadingMore ? <ActivityIndicator color={colors.text} style={styles.pickerLoader} /> : null}
            ListEmptyComponent={<StateView title="Kein kochfertiges Rezept gefunden" message="Versuche einen anderen Suchbegriff." />}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: {
    paddingHorizontal: space.md,
    paddingTop: 10,
    paddingBottom: 10,
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
  },
  eyebrow: { color: colors.muted, fontSize: 11, letterSpacing: 1.5, fontWeight: '800' },
  title: { color: colors.text, fontSize: 34, letterSpacing: -1, fontWeight: '900' },
  count: { color: colors.text, paddingBottom: 5, fontWeight: '800' },
  weekNav: { paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  navButton: { minHeight: 44, justifyContent: 'center' },
  navText: { color: colors.text, fontWeight: '700' },
  weekText: { color: colors.muted, fontSize: 13 },
  todayButton: { minHeight: 44, alignSelf: 'center', justifyContent: 'center', paddingHorizontal: space.md },
  todayText: { color: colors.text, fontSize: 13, fontWeight: '800' },
  list: { padding: space.md, paddingTop: 6, paddingBottom: 120 },
  day: {
    padding: 14,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    gap: 10,
  },
  today: { borderColor: colors.butterPressed, borderWidth: 2 },
  dayHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  dayLabel: { color: colors.text, fontSize: 19, fontWeight: '800' },
  dayDate: { color: colors.muted, fontSize: 12, marginTop: 2 },
  plus: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 14, backgroundColor: colors.butter },
  plusText: { color: colors.text, fontSize: 25, fontWeight: '700' },
  meal: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 6 },
  mealText: { flex: 1 },
  mealName: { color: colors.text, fontSize: 16, fontWeight: '700' },
  servings: { color: colors.muted, marginTop: 2, fontSize: 12 },
  smallButton: { width: 38, height: 44, alignItems: 'center', justifyContent: 'center' },
  remove: { color: colors.danger, fontSize: 22 },
  emptyDay: { color: colors.muted, paddingVertical: 8 },
  footer: { paddingTop: space.lg, gap: 10 },
  preview: { color: colors.muted, textAlign: 'center' },
  picker: { flex: 1, backgroundColor: colors.cream },
  pickerHeader: {
    minHeight: 54,
    paddingHorizontal: space.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  pickerTitle: { color: colors.text, fontSize: 16, fontWeight: '800' },
  search: {
    minHeight: 48,
    margin: space.md,
    marginTop: 4,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    color: colors.text,
    fontSize: 16,
  },
  pickerList: { paddingHorizontal: space.md, paddingBottom: 40 },
  pickerLoader: { paddingVertical: space.lg },
  pickerItem: { minHeight: 58, flexDirection: 'row', alignItems: 'center' },
  pickerName: { flex: 1, color: colors.text, fontSize: 17, fontWeight: '600' },
  chevron: { color: colors.muted, fontSize: 26 },
  line: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border },
  pickerError: { color: colors.danger, paddingHorizontal: space.md, paddingBottom: 8 },
});
