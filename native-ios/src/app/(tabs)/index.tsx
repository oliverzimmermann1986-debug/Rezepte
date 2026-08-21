import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FlatList,
  Pressable,
  RefreshControl,
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
import { RecipeListItem } from '@/lib/types';

type RecipeResponse = { total: number; items: RecipeListItem[] };

export default function RecipesScreen() {
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [query, setQuery] = useState('');
  const [manualOnly, setManualOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);

  const load = useCallback(async (search = query, refresh = false) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '200' });
      if (search.trim()) params.set('search', search.trim());
      const result = await api<RecipeResponse>(`/api/recipes?${params}`, {}, controller.signal);
      setRecipes(result.items);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Laden fehlgeschlagen');
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [query]);

  useEffect(() => {
    const timer = setTimeout(() => load(query), 250);
    return () => {
      clearTimeout(timer);
      request.current?.abort();
    };
  }, [load, query]);

  const visible = manualOnly ? recipes.filter(item => item.needs_manual_care) : recipes;

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.eyebrow}>DEINE KÜCHE</Text>
          <Text style={styles.title}>Rezepte</Text>
        </View>
        <Text style={styles.count}>{visible.length}</Text>
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
          accessibilityRole="checkbox"
          accessibilityState={{ checked: manualOnly }}
          onPress={() => setManualOnly(value => !value)}
          style={({ pressed }) => [styles.filter, manualOnly && styles.filterActive, pressed && styles.pressed]}>
          <Text style={[styles.filterText, manualOnly && styles.filterTextActive]}>⚠ Pflegen</Text>
        </Pressable>
      </View>
      {loading && !recipes.length ? (
        <StateView title="Rezepte werden geladen" loading />
      ) : error && !recipes.length ? (
        <StateView title="Keine Verbindung" message={error} action="Erneut versuchen" onAction={() => load()} />
      ) : (
        <FlatList
          data={visible}
          keyExtractor={item => String(item.id)}
          renderItem={({ item }) => <RecipeCard recipe={item} />}
          contentContainerStyle={styles.list}
          ItemSeparatorComponent={() => <View style={{ height: space.md }} />}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={() => load(query, true)} tintColor={colors.text} />
          }
          ListEmptyComponent={
            <StateView
              title={manualOnly ? 'Alles gepflegt' : 'Keine Rezepte gefunden'}
              message={manualOnly ? 'Kein Rezept benötigt gerade manuelle Pflege.' : 'Versuche einen anderen Suchbegriff.'}
            />
          }
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
    paddingHorizontal: 13,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  filterActive: { backgroundColor: colors.warningSurface, borderColor: '#D69A48' },
  filterText: { color: colors.muted, fontWeight: '700' },
  filterTextActive: { color: colors.warning },
  pressed: { opacity: 0.7 },
  list: { padding: space.md, paddingTop: 4, paddingBottom: 120 },
});
