import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import { SymbolView } from 'expo-symbols';
import {
  ActivityIndicator,
  Alert,
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
import { useAuth } from '@/lib/auth-context';
import { apiCached, invalidateApiCacheByPrefix } from '@/lib/cache';
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
  categories: string[];
  tagIds: number[];
  includedIngredients: string[];
  excludedIngredients: string[];
  favoriteOnly: boolean;
  manualOnly: boolean;
  ratings: number[];
};

const EMPTY_FILTERS: RecipeFilters = {
  type: '',
  categories: [],
  tagIds: [],
  includedIngredients: [],
  excludedIngredients: [],
  favoriteOnly: false,
  manualOnly: false,
  ratings: [],
};
const PAGE_SIZE = 60;

function appendRecipeFilters(params: URLSearchParams, filters: RecipeFilters) {
  if (filters.type) params.set('type', filters.type);
  filters.categories.forEach(value => params.append('category', value));
  if (filters.favoriteOnly) params.set('favorite_only', 'true');
  if (filters.manualOnly) params.set('needs_manual_care', 'true');
  filters.ratings.forEach(value => params.append('rating', String(value)));
  filters.tagIds.forEach(value => params.append('tag_id', String(value)));
  filters.includedIngredients.forEach(value => params.append('ingredient', value));
  filters.excludedIngredients.forEach(value => params.append('exclude_ingredient', value));
}

function activeFilterCount(filters: RecipeFilters) {
  return [
    Boolean(filters.type),
    ...filters.categories.map(() => true),
    ...filters.tagIds.map(() => true),
    ...filters.includedIngredients.map(() => true),
    ...filters.excludedIngredients.map(() => true),
    filters.favoriteOnly,
    filters.manualOnly,
    ...filters.ratings.map(() => true),
  ].filter(Boolean).length;
}

function toggleValue<T>(values: T[], value: T) {
  return values.includes(value)
    ? values.filter(item => item !== value)
    : [...values, value];
}

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
  const { isAdmin, refreshSession, sessionChecking, sessionWarning, signOut } = useAuth();
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<RecipeFilters>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<RecipeFilters>(EMPTY_FILTERS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [facets, setFacets] = useState<RecipeFacets | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(false);
  const [facetsError, setFacetsError] = useState('');
  const [draftTotal, setDraftTotal] = useState<number | null>(null);
  const [draftTotalLoading, setDraftTotalLoading] = useState(false);
  const [draftTotalError, setDraftTotalError] = useState('');
  const [expandedAdvanced, setExpandedAdvanced] = useState<'tags' | 'ingredients' | null>(null);
  const [ingredientQuery, setIngredientQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const request = useRef<AbortController | null>(null);
  const countRequest = useRef<AbortController | null>(null);
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
      appendRecipeFilters(params, active);
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
    if (sessionWarning) void refreshSession();
    if (initialLoadDone.current) {
      setFacets(null);
      void load({ refresh: recipesRef.current.length > 0 });
    }
    return () => request.current?.abort();
  }, [load, refreshSession, sessionWarning]));

  const appliedFilterCount = activeFilterCount(filters);

  async function openFilters() {
    setDraftFilters(filters);
    setDraftTotal(total);
    setDraftTotalError('');
    setExpandedAdvanced(
      filters.includedIngredients.length || filters.excludedIngredients.length
        ? 'ingredients'
        : filters.tagIds.length ? 'tags' : null,
    );
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

  const draftActiveFilterCount = activeFilterCount(draftFilters);

  useEffect(() => {
    if (!filterOpen) {
      countRequest.current?.abort();
      return;
    }
    const controller = new AbortController();
    countRequest.current?.abort();
    countRequest.current = controller;
    const timer = setTimeout(async () => {
      setDraftTotalLoading(true);
      setDraftTotalError('');
      try {
        const params = new URLSearchParams();
        if (query.trim()) params.set('search', query.trim());
        appendRecipeFilters(params, draftFilters);
        const result = await api<{ total: number }>(
          `/api/recipes/count?${params}`,
          {},
          controller.signal,
        );
        if (!controller.signal.aborted) setDraftTotal(result.total);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setDraftTotalError(reason instanceof Error ? reason.message : 'Trefferzahl nicht verfügbar');
        }
      } finally {
        if (!controller.signal.aborted) setDraftTotalLoading(false);
      }
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [draftFilters, filterOpen, query]);

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

  function confirmDeleteRecipe(recipe: RecipeListItem) {
    Alert.alert(
      'Rezept in den Papierkorb?',
      `„${recipe.name}“ verschwindet aus der Rezeptliste und kann 30 Tage lang im Admin-Bereich wiederhergestellt werden.`,
      [
        { text: 'Abbrechen', style: 'cancel' },
        {
          text: 'In Papierkorb',
          style: 'destructive',
          onPress: () => void deleteRecipe(recipe),
        },
      ],
    );
  }

  async function deleteRecipe(recipe: RecipeListItem) {
    if (deletingId !== null) return;
    setDeletingId(recipe.id);
    try {
      await api(`/api/recipes/${recipe.id}?delete_files=true`, { method: 'DELETE' });
      await invalidateApiCacheByPrefix('recipe:', 'recipes:', 'recipe-facets', 'meal-plan', 'cart', 'admin:');
      setRecipes(current => current.filter(item => item.id !== recipe.id));
      setTotal(current => Math.max(0, current - 1));
      setFacets(null);
      Alert.alert(
        'In Papierkorb verschoben',
        `„${recipe.name}“ kann im Admin-Bereich 30 Tage lang wiederhergestellt werden.`,
      );
    } catch (reason) {
      Alert.alert(
        'Rezept nicht gelöscht',
        reason instanceof Error ? reason.message : 'Bitte erneut versuchen.',
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>DEINE KÜCHE</Text>
          <Text style={styles.title}>Rezepte</Text>
        </View>
        <View style={styles.headerActions}>
          <Text accessibilityLabel={`${total} Rezepte`} style={styles.count}>{total}</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Abmelden"
            onPress={() => void signOut()}
            style={({ pressed }) => [styles.signOut, pressed && styles.pressed]}>
            <SymbolView name="rectangle.portrait.and.arrow.right" size={16} tintColor={colors.danger} />
            <Text style={styles.signOutText}>Abmelden</Text>
          </Pressable>
        </View>
      </View>
      {!!sessionWarning && (
        <View accessibilityRole="alert" style={styles.sessionBanner}>
          <Text style={styles.sessionWarning}>{sessionWarning}</Text>
          <Pressable
            accessibilityRole="button"
            disabled={sessionChecking}
            onPress={() => void refreshSession()}
            style={({ pressed }) => [
              styles.sessionRetry,
              sessionChecking && styles.disabled,
              pressed && styles.pressed,
            ]}>
            {sessionChecking && <ActivityIndicator color={colors.text} size="small" />}
            <Text style={styles.sessionRetryText}>
              {sessionChecking ? 'Wird geprüft …' : 'Verbindung prüfen'}
            </Text>
          </Pressable>
        </View>
      )}
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
          accessibilityLabel={appliedFilterCount ? `Filter, ${appliedFilterCount} aktiv` : 'Filter'}
          onPress={openFilters}
          style={({ pressed }) => [styles.filter, appliedFilterCount > 0 && styles.filterActive, pressed && styles.pressed]}>
          <SymbolView name="line.3.horizontal.decrease" size={18} tintColor={colors.text} />
          <Text style={styles.filterText}>Filter</Text>
          {appliedFilterCount > 0 && <Text style={styles.filterCount}>{appliedFilterCount}</Text>}
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
          renderItem={({ item }) => (
            <RecipeCard
              recipe={item}
              deleting={deletingId === item.id}
              onDelete={isAdmin ? confirmDeleteRecipe : undefined}
            />
          )}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: space.md }} />}
          refreshControl={
            <RefreshControl
              refreshing={refreshing || sessionChecking}
              onRefresh={() => {
                void refreshSession();
                void load({ search: query, refresh: true });
              }}
              tintColor={colors.text}
            />
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
              message={appliedFilterCount ? 'Passe die aktiven Filter an.' : 'Versuche einen anderen Suchbegriff.'}
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
            <Text style={styles.sheetTitle}>Filter</Text>
            <Pressable accessibilityRole="button" onPress={() => setDraftFilters({ ...EMPTY_FILTERS, categories: [], ratings: [], tagIds: [], includedIngredients: [], excludedIngredients: [] })} style={styles.sheetHeaderAction}>
              <Text style={styles.sheetReset}>Zurücksetzen</Text>
            </Pressable>
          </View>
          <ScrollView
            automaticallyAdjustKeyboardInsets
            keyboardDismissMode="interactive"
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={styles.sheetContent}>
            {facetsLoading && <StateView title="Filter werden geladen" loading />}
            {!!facetsError && <StateView title="Filter nicht verfügbar" message={facetsError} action="Erneut versuchen" onAction={() => { setFacets(null); openFilters(); }} />}

            <View accessibilityLiveRegion="polite" style={styles.filterSummary}>
              <View>
                <Text style={styles.filterTotal}>{draftTotal ?? '–'}</Text>
                <Text style={styles.filterTotalLabel}>{draftTotal === 1 ? 'Rezept gefunden' : 'Rezepte gefunden'}</Text>
              </View>
              <Text style={styles.activeSummary}>
                {draftActiveFilterCount ? `${draftActiveFilterCount} ausgewählt` : 'Keine Filter aktiv'}
              </Text>
            </View>
            {!!draftTotalError && <Text accessibilityRole="alert" style={styles.countError}>{draftTotalError}</Text>}

            <View style={styles.filterSection}>
              <Text style={styles.filterSectionTitle}>Status</Text>
              <Text style={styles.filterHelp}>Häufig verwendete Filter für den schnellen Zugriff.</Text>
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

            {(!!facets?.types.length || !!facets?.categories.length) && (
              <View style={styles.filterSection}>
                <Text style={styles.filterSectionTitle}>Gericht</Text>
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
                    <Text style={styles.filterHeading}>Kategorien</Text>
                    <Text style={styles.filterHelp}>Mehrere Kategorien werden gemeinsam angezeigt.</Text>
                    <View style={styles.chipRow}>
                      <FilterChip label="Alle" selected={!draftFilters.categories.length} onPress={() => setDraftFilters(value => ({ ...value, categories: [] }))} />
                      {facets.categories.map(value => (
                        <FilterChip
                          key={value}
                          label={value}
                          selected={draftFilters.categories.includes(value)}
                          onPress={() => setDraftFilters(current => ({ ...current, categories: toggleValue(current.categories, value) }))}
                        />
                      ))}
                    </View>
                  </View>
                )}
              </View>
            )}

            <View style={styles.filterSection}>
              <Text style={styles.filterSectionTitle}>Bewertung</Text>
              <Text style={styles.filterHelp}>Wähle eine oder mehrere genaue Bewertungen.</Text>
              <View style={styles.chipRow}>
                <FilterChip label="Alle" selected={!draftFilters.ratings.length} onPress={() => setDraftFilters(value => ({ ...value, ratings: [] }))} />
                {[5, 4, 3, 2, 1].map(value => (
                  <FilterChip
                    key={value}
                    label={`${value} ★`}
                    selected={draftFilters.ratings.includes(value)}
                    onPress={() => setDraftFilters(current => ({ ...current, ratings: toggleValue(current.ratings, value) }))}
                  />
                ))}
                <FilterChip
                  label="Unbewertet"
                  selected={draftFilters.ratings.includes(0)}
                  onPress={() => setDraftFilters(current => ({ ...current, ratings: toggleValue(current.ratings, 0) }))}
                />
              </View>
            </View>

            {(!!facets?.tags.length || !!facets?.ingredients.length) && (
              <View style={styles.filterSection}>
                <Text style={styles.filterSectionTitle}>Weitere Filter</Text>
                {!!facets?.tags.length && (
                  <>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{ expanded: expandedAdvanced === 'tags' }}
                      onPress={() => setExpandedAdvanced(value => value === 'tags' ? null : 'tags')}
                      style={({ pressed }) => [styles.disclosure, pressed && styles.pressed]}>
                      <View>
                        <Text style={styles.disclosureTitle}>Tags</Text>
                        <Text style={styles.disclosureMeta}>{draftFilters.tagIds.length ? `${draftFilters.tagIds.length} ausgewählt` : 'Optional'}</Text>
                      </View>
                      <SymbolView name={expandedAdvanced === 'tags' ? 'chevron.up' : 'chevron.down'} size={17} tintColor={colors.text} />
                    </Pressable>
                    {expandedAdvanced === 'tags' && (
                      <View style={styles.disclosureContent}>
                        <Text style={styles.filterHelp}>Mehrere Tags können gleichzeitig ausgewählt werden.</Text>
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
                  </>
                )}

                {!!facets?.ingredients.length && (
                  <>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{ expanded: expandedAdvanced === 'ingredients' }}
                      onPress={() => setExpandedAdvanced(value => value === 'ingredients' ? null : 'ingredients')}
                      style={({ pressed }) => [styles.disclosure, pressed && styles.pressed]}>
                      <View>
                        <Text style={styles.disclosureTitle}>Zutaten</Text>
                        <Text style={styles.disclosureMeta}>
                          {draftFilters.includedIngredients.length + draftFilters.excludedIngredients.length
                            ? `${draftFilters.includedIngredients.length + draftFilters.excludedIngredients.length} ausgewählt`
                            : 'Mit oder ohne Zutat'}
                        </Text>
                      </View>
                      <SymbolView name={expandedAdvanced === 'ingredients' ? 'chevron.up' : 'chevron.down'} size={17} tintColor={colors.text} />
                    </Pressable>
                    {expandedAdvanced === 'ingredients' && (
                      <View style={styles.disclosureContent}>
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
                  </>
                )}
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
              {draftTotalLoading && <ActivityIndicator color={colors.text} size="small" />}
              <Text style={styles.applyText}>
                {draftTotal === null ? 'Filter anwenden' : draftTotal === 1 ? '1 Rezept anzeigen' : `${draftTotal} Rezepte anzeigen`}
              </Text>
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
  headerActions: { alignItems: 'flex-end', gap: 4 },
  signOut: { minHeight: 44, paddingHorizontal: 4, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  signOutText: { color: colors.danger, fontSize: 14, fontWeight: '800' },
  sessionBanner: { marginHorizontal: space.md, marginBottom: 12, padding: 12, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  sessionWarning: { flexGrow: 1, flexShrink: 1, minWidth: 180, color: colors.text, lineHeight: 20 },
  sessionRetry: { minHeight: 44, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7 },
  sessionRetryText: { color: colors.text, fontWeight: '900' },
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
  disabled: { opacity: 0.45 },
  list: { padding: space.md, paddingTop: 4, paddingBottom: 120 },
  pageLoader: { paddingVertical: space.lg },
  sheetSafe: { flex: 1, backgroundColor: colors.cream },
  sheetHeader: { minHeight: 58, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  sheetHeaderAction: { minWidth: 82, minHeight: 44, justifyContent: 'center' },
  sheetTitle: { color: colors.text, fontSize: 17, fontWeight: '900' },
  sheetCancel: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  sheetReset: { color: colors.text, fontSize: 14, fontWeight: '800', textAlign: 'right' },
  sheetContent: { padding: space.md, paddingBottom: space.xl, gap: space.lg },
  filterSummary: { minHeight: 84, paddingHorizontal: space.md, paddingVertical: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 16, borderRadius: radii.md, backgroundColor: colors.butter },
  filterTotal: { color: colors.text, fontSize: 30, lineHeight: 32, letterSpacing: -0.8, fontWeight: '900' },
  filterTotalLabel: { color: colors.text, fontSize: 13, fontWeight: '700' },
  activeSummary: { flexShrink: 1, color: colors.text, fontSize: 14, lineHeight: 19, fontWeight: '800', textAlign: 'right' },
  countError: { marginTop: -12, color: colors.danger, fontSize: 13, lineHeight: 18 },
  filterSection: { gap: 14, paddingBottom: space.lg, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  filterSectionTitle: { color: colors.text, fontSize: 21, fontWeight: '900' },
  filterGroup: { gap: 10 },
  filterHeading: { color: colors.text, fontSize: 17, fontWeight: '900' },
  filterHelp: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 44, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: 22, backgroundColor: colors.surface },
  chipActive: { borderColor: colors.butterPressed, backgroundColor: colors.butter },
  chipText: { color: colors.muted, fontSize: 15, fontWeight: '700' },
  chipTextActive: { color: colors.text, fontWeight: '900' },
  disclosure: { minHeight: 56, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  disclosureTitle: { color: colors.text, fontSize: 16, fontWeight: '900' },
  disclosureMeta: { marginTop: 2, color: colors.muted, fontSize: 13, lineHeight: 18 },
  disclosureContent: { gap: 12, paddingBottom: 8 },
  ingredientSearch: { minHeight: 48, paddingHorizontal: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.white, color: colors.text, fontSize: 16 },
  ingredientFilterRow: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  ingredientFilterName: { flex: 1, color: colors.text, fontSize: 15, fontWeight: '700' },
  ingredientCount: { color: colors.muted, fontWeight: '600' },
  ingredientChoices: { flexDirection: 'row', gap: 6 },
  sheetFooter: { padding: space.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, backgroundColor: colors.surface },
  applyButton: { minHeight: 52, flexDirection: 'row', gap: 9, alignItems: 'center', justifyContent: 'center', borderRadius: radii.md, backgroundColor: colors.butter },
  applyText: { color: colors.text, fontSize: 16, fontWeight: '900' },
});
