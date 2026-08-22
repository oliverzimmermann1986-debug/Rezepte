import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { SymbolView } from 'expo-symbols';
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { RecipeCard } from '@/components/recipe-card';
import { StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { apiCached } from '@/lib/cache';
import { RecipeListItem } from '@/lib/types';

type RecipeResponse = { total: number; items: RecipeListItem[] };
type RecipeFacets = {
  types: string[];
  categories: string[];
  tags: { id: number; name: string; n: number }[];
  ingredients: { canonical_name: string; display_name: string; n: number }[];
};
type RecipeFilters = {
  type: string;
  category: string;
  tagIds: number[];
  includedIngredients: string[];
  excludedIngredients: string[];
  favoriteOnly: boolean;
  manualOnly: boolean;
  minRating: number;
};

const EMPTY_FILTERS: RecipeFilters = {
  type: '',
  category: '',
  tagIds: [],
  includedIngredients: [],
  excludedIngredients: [],
  favoriteOnly: false,
  manualOnly: false,
  minRating: 0,
};
const PAGE_SIZE = 60;

function FilterChip({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={({ pressed }) => [styles.chip, selected && styles.chipActive, pressed && styles.pressed]}>
      <Text style={[styles.chipText, selected && styles.chipTextActive]}>{label}</Text>
    </Pressable>
  );
}

export default function RecipesScreen() {
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<RecipeFilters>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<RecipeFilters>(EMPTY_FILTERS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [facets, setFacets] = useState<RecipeFacets | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(false);
  const [facetsError, setFacetsError] = useState('');
  const [ingredientQuery, setIngredientQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const recipesRef = useRef<RecipeListItem[]>([]);
  const filtersRef = useRef(filters);
  const queryRef = useRef(query);
  const initialLoadDone = useRef(false);
  const loadingMoreRef = useRef(false);

  useEffect(() => { recipesRef.current = recipes; }, [recipes]);
  useEffect(() => { filtersRef.current = filters; }, [filters]);
  useEffect(() => { queryRef.current = query; }, [query]);

  const load = useCallback(async ({
    search = queryRef.current,
    refresh = false,
    append = false,
  }: { search?: string; refresh?: boolean; append?: boolean } = {}) => {
    if (append && loadingMoreRef.current) return;
    const generation = ++requestGeneration.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    if (append) {
      loadingMoreRef.current = true;
      setLoadingMore(true);
    } else if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError('');
    try {
      const active = filtersRef.current;
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(append ? recipesRef.current.length : 0),
      });
      if (search.trim()) params.set('search', search.trim());
      if (active.type) params.set('type', active.type);
      if (active.category) params.set('category', active.category);
      if (active.favoriteOnly) params.set('favorite_only', 'true');
      if (active.manualOnly) params.set('needs_manual_care', 'true');
      if (active.minRating) params.set('min_rating', String(active.minRating));
      active.tagIds.forEach(value => params.append('tag_id', String(value)));
      active.includedIngredients.forEach(value => params.append('ingredient', value));
      active.excludedIngredients.forEach(value => params.append('exclude_ingredient', value));
      const path = `/api/recipes?${params}`;
      const result = append
        ? await api<RecipeResponse>(path, {}, controller.signal)
        : await apiCached<RecipeResponse>(`recipes:${params}`, path, controller.signal);
      if (generation !== requestGeneration.current) return;
      if (append) {
        setRecipes(current => {
          const known = new Set(current.map(item => item.id));
          return [...current, ...result.items.filter(item => !known.has(item.id))];
        });
      } else {
        setRecipes(result.items);
      }
      setTotal(result.total);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Laden fehlgeschlagen');
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false);
        setRefreshing(false);
        loadingMoreRef.current = false;
        setLoadingMore(false);
        initialLoadDone.current = true;
      }
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load({ search: query }), 250);
    return () => {
      clearTimeout(timer);
      request.current?.abort();
    };
  }, [filters, load, query]);

  useFocusEffect(useCallback(() => {
    if (initialLoadDone.current) {
      setFacets(null);
      void load({ refresh: recipesRef.current.length > 0 });
    }
    return () => request.current?.abort();
  }, [load]));

  const activeFilterCount = [
    Boolean(filters.type),
    Boolean(filters.category),
    ...filters.tagIds.map(() => true),
    ...filters.includedIngredients.map(() => true),
    ...filters.excludedIngredients.map(() => true),
    filters.favoriteOnly,
    filters.manualOnly,
    filters.minRating > 0,
  ].filter(Boolean).length;

  async function openFilters() {
    setDraftFilters(filters);
    setIngredientQuery('');
    setFilterOpen(true);
    if (facets || facetsLoading) return;
    setFacetsLoading(true);
    setFacetsError('');
    try {
      setFacets(await apiCached<RecipeFacets>('recipe-facets', '/api/recipes/facets'));
    } catch (reason) {
      setFacetsError(reason instanceof Error ? reason.message : 'Filter konnten nicht geladen werden');
    } finally {
      setFacetsLoading(false);
    }
  }

  const draftActiveFilterCount = [
    Boolean(draftFilters.type),
    Boolean(draftFilters.category),
    ...draftFilters.tagIds.map(() => true),
    ...draftFilters.includedIngredients.map(() => true),
    ...draftFilters.excludedIngredients.map(() => true),
    draftFilters.favoriteOnly,
    draftFilters.manualOnly,
    draftFilters.minRating > 0,
  ].filter(Boolean).length;

  const filteredIngredients = (facets?.ingredients || []).filter(ingredient => {
    const needle = ingredientQuery.trim().toLocaleLowerCase('de');
    if (!needle) return true;
    return ingredient.display_name.toLocaleLowerCase('de').includes(needle)
      || ingredient.canonical_name.toLocaleLowerCase('de').includes(needle);
  });

  function toggleTag(id: number) {
    setDraftFilters(current => ({
      ...current,
      tagIds: current.tagIds.includes(id)
        ? current.tagIds.filter(value => value !== id)
        : [...current.tagIds, id],
    }));
  }

  function setIngredientChoice(name: string, choice: 'include' | 'exclude') {
    setDraftFilters(current => {
      const includedIngredients = current.includedIngredients.filter(value => value !== name);
      const excludedIngredients = current.excludedIngredients.filter(value => value !== name);
      if (choice === 'include' && !current.includedIngredients.includes(name)) includedIngredients.push(name);
      if (choice === 'exclude' && !current.excludedIngredients.includes(name)) excludedIngredients.push(name);
      return { ...current, includedIngredients, excludedIngredients };
    });
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>DEINE KÜCHE</Text>
          <Text style={styles.title}>Rezepte</Text>
        </View>
        <Text style={styles.count}>{total}</Text>
      </View>
      <View style={styles.controls}>
        <TextInput
          accessibilityLabel="Rezepte durchsuchen"
          autoCorrect={false}
          placeholder="Gericht oder Zutat suchen"
          placeholderTextColor={colors.muted}
          returnKeyType="search"
          value={query}
          onChangeText={setQuery}
          style={styles.search}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={activeFilterCount ? `Filter, ${activeFilterCount} aktiv` : 'Filter'}
          onPress={openFilters}
          style={({ pressed }) => [styles.filter, activeFilterCount > 0 && styles.filterActive, pressed && styles.pressed]}>
          <SymbolView name="line.3.horizontal.decrease" size={18} tintColor={colors.text} />
          <Text style={styles.filterText}>Filter</Text>
          {activeFilterCount > 0 && <Text style={styles.filterCount}>{activeFilterCount}</Text>}
        </Pressable>
      </View>
      {loading && !recipes.length ? (
        <StateView title="Rezepte werden geladen" loading />
      ) : error && !recipes.length ? (
        <StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => load()} />
      ) : (
        <FlatList
          data={recipes}
          keyExtractor={item => String(item.id)}
          renderItem={({ item }) => <RecipeCard recipe={item} />}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: space.md }} />}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => load({ search: query, refresh: true })} tintColor={colors.text} />
          }
          onEndReachedThreshold={0.4}
          onEndReached={() => {
            if (!loading && !refreshing && !loadingMore && recipes.length < total) {
              void load({ append: true });
            }
          }}
          ListFooterComponent={loadingMore ? <ActivityIndicator color={colors.text} style={styles.pageLoader} /> : null}
          ListEmptyComponent={
            <StateView
              title={filters.manualOnly ? 'Alles gepflegt' : 'Keine Rezepte gefunden'}
              message={activeFilterCount ? 'Passe die aktiven Filter an.' : 'Versuche einen anderen Suchbegriff.'}
            />
          }
        />
      )}
      <Modal
        animationType="slide"
        presentationStyle="pageSheet"
        visible={filterOpen}
        onRequestClose={() => setFilterOpen(false)}>
        <SafeAreaView style={styles.sheetSafe} edges={['top', 'bottom', 'left', 'right']}>
          <View style={styles.sheetHeader}>
            <Pressable accessibilityRole="button" onPress={() => setFilterOpen(false)} style={styles.sheetHeaderAction}>
              <Text style={styles.sheetCancel}>Abbrechen</Text>
            </Pressable>
            <Text style={styles.sheetTitle}>{draftActiveFilterCount ? `${draftActiveFilterCount} aktiv` : 'Rezepte filtern'}</Text>
            <Pressable accessibilityRole="button" onPress={() => setDraftFilters({ ...EMPTY_FILTERS, tagIds: [], includedIngredients: [], excludedIngredients: [] })} style={styles.sheetHeaderAction}>
              <Text style={styles.sheetReset}>Zurücksetzen</Text>
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.sheetContent}>
            <View style={styles.filterGroup}>
              <Text style={styles.filterHeading}>Schnellfilter</Text>
              <View style={styles.chipRow}>
                <FilterChip
                  label="Nur Favoriten"
                  selected={draftFilters.favoriteOnly}
                  onPress={() => setDraftFilters(value => ({ ...value, favoriteOnly: !value.favoriteOnly }))}
                />
                <FilterChip
                  label="Manuell pflegen"
                  selected={draftFilters.manualOnly}
                  onPress={() => setDraftFilters(value => ({ ...value, manualOnly: !value.manualOnly }))}
                />
              </View>
            </View>

            <View style={styles.filterGroup}>
              <Text style={styles.filterHeading}>Bewertung</Text>
              <View style={styles.chipRow}>
                {[0, 1, 2, 3, 4, 5].map(value => (
                  <FilterChip
                    key={value}
                    label={value === 0 ? 'Alle' : `${value}+ ★`}
                    selected={draftFilters.minRating === value}
                    onPress={() => setDraftFilters(current => ({ ...current, minRating: value }))}
                  />
                ))}
              </View>
            </View>

            {facetsLoading && <StateView title="Filter werden geladen" loading />}
            {!!facetsError && <StateView title="Filter nicht verfügbar" message={facetsError} action="Erneut versuchen" onAction={() => { setFacets(null); openFilters(); }} />}

            {!!facets?.types.length && (
              <View style={styles.filterGroup}>
                <Text style={styles.filterHeading}>Typ</Text>
                <View style={styles.chipRow}>
                  <FilterChip label="Alle" selected={!draftFilters.type} onPress={() => setDraftFilters(value => ({ ...value, type: '' }))} />
                  {facets.types.map(value => (
                    <FilterChip key={value} label={value} selected={draftFilters.type === value} onPress={() => setDraftFilters(current => ({ ...current, type: value }))} />
                  ))}
                </View>
              </View>
            )}

            {!!facets?.categories.length && (
              <View style={styles.filterGroup}>
                <Text style={styles.filterHeading}>Kategorie</Text>
                <View style={styles.chipRow}>
                  <FilterChip label="Alle" selected={!draftFilters.category} onPress={() => setDraftFilters(value => ({ ...value, category: '' }))} />
                  {facets.categories.map(value => (
                    <FilterChip key={value} label={value} selected={draftFilters.category === value} onPress={() => setDraftFilters(current => ({ ...current, category: value }))} />
                  ))}
                </View>
              </View>
            )}

            {!!facets?.tags.length && (
              <View style={styles.filterGroup}>
                <Text style={styles.filterHeading}>Tags</Text>
                <View style={styles.chipRow}>
                  {facets.tags.map(tag => (
                    <FilterChip
                      key={tag.id}
                      label={`${tag.name} · ${tag.n}`}
                      selected={draftFilters.tagIds.includes(tag.id)}
                      onPress={() => toggleTag(tag.id)}
                    />
                  ))}
                </View>
              </View>
            )}

            {!!facets?.ingredients.length && (
              <View style={styles.filterGroup}>
                <Text style={styles.filterHeading}>Zutaten</Text>
                <TextInput
                  accessibilityLabel="Filterzutaten durchsuchen"
                  autoCorrect={false}
                  placeholder="Zutat suchen"
                  placeholderTextColor={colors.muted}
                  value={ingredientQuery}
                  onChangeText={setIngredientQuery}
                  style={styles.ingredientSearch}
                />
                <Text style={styles.filterHelp}>„Mit“ verlangt die Zutat, „Ohne“ schließt sie aus.</Text>
                {filteredIngredients.map(ingredient => (
                  <View key={ingredient.canonical_name} style={styles.ingredientFilterRow}>
                    <Text style={styles.ingredientFilterName} numberOfLines={2}>
                      {ingredient.display_name} <Text style={styles.ingredientCount}>· {ingredient.n}</Text>
                    </Text>
                    <View style={styles.ingredientChoices}>
                      <FilterChip
                        label="Mit"
                        selected={draftFilters.includedIngredients.includes(ingredient.canonical_name)}
                        onPress={() => setIngredientChoice(ingredient.canonical_name, 'include')}
                      />
                      <FilterChip
                        label="Ohne"
                        selected={draftFilters.excludedIngredients.includes(ingredient.canonical_name)}
                        onPress={() => setIngredientChoice(ingredient.canonical_name, 'exclude')}
                      />
                    </View>
                  </View>
                ))}
              </View>
            )}
          </ScrollView>
          <View style={styles.sheetFooter}>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setFilters(draftFilters);
                setFilterOpen(false);
              }}
              style={({ pressed }) => [styles.applyButton, pressed && styles.pressed]}>
              <Text style={styles.applyText}>Filter anwenden</Text>
            </Pressable>
          </View>
        </SafeAreaView>
      </Modal>
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
  count: { color: colors.text, fontSize: 17, fontWeight: '800', paddingBottom: 5 },
  controls: { paddingHorizontal: space.md, paddingBottom: 12, flexDirection: 'row', gap: 8 },
  search: {
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    color: colors.text,
    fontSize: 16,
  },
  filter: {
    minHeight: 48,
    paddingHorizontal: 12,
    flexDirection: 'row',
    gap: 7,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  filterActive: { backgroundColor: colors.butter, borderColor: colors.butterPressed },
  filterText: { color: colors.text, fontWeight: '800' },
  filterCount: { minWidth: 20, height: 20, paddingTop: 2, borderRadius: 10, overflow: 'hidden', color: colors.surface, backgroundColor: colors.text, fontSize: 12, fontWeight: '900', textAlign: 'center' },
  pressed: { opacity: 0.7 },
  list: { padding: space.md, paddingTop: 4, paddingBottom: 120 },
  pageLoader: { paddingVertical: space.lg },
  sheetSafe: { flex: 1, backgroundColor: colors.cream },
  sheetHeader: { minHeight: 58, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  sheetHeaderAction: { minWidth: 82, minHeight: 44, justifyContent: 'center' },
  sheetTitle: { color: colors.text, fontSize: 17, fontWeight: '900' },
  sheetCancel: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  sheetReset: { color: colors.text, fontSize: 14, fontWeight: '800', textAlign: 'right' },
  sheetContent: { padding: space.md, paddingBottom: space.xl, gap: space.lg },
  filterGroup: { gap: 10 },
  filterHeading: { color: colors.text, fontSize: 17, fontWeight: '900' },
  filterHelp: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 44, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: 22, backgroundColor: colors.surface },
  chipActive: { borderColor: colors.butterPressed, backgroundColor: colors.butter },
  chipText: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  chipTextActive: { color: colors.text, fontWeight: '900' },
  ingredientSearch: { minHeight: 48, paddingHorizontal: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.white, color: colors.text, fontSize: 16 },
  ingredientFilterRow: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  ingredientFilterName: { flex: 1, color: colors.text, fontSize: 15, fontWeight: '700' },
  ingredientCount: { color: colors.muted, fontWeight: '600' },
  ingredientChoices: { flexDirection: 'row', gap: 6 },
  sheetFooter: { padding: space.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, backgroundColor: colors.surface },
  applyButton: { minHeight: 52, alignItems: 'center', justifyContent: 'center', borderRadius: radii.md, backgroundColor: colors.butter },
  applyText: { color: colors.text, fontSize: 16, fontWeight: '900' },
});
