import { SymbolView } from 'expo-symbols';
import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton, StateView, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { RecipeListItem } from '@/lib/types';

type RecipeResponse = { total: number; items: RecipeListItem[] };
type RecipeFacets = {
  categories: string[];
  tags: { id: number; name: string; n: number }[];
};
type BulkResult = {
  ok: boolean;
  updated: { recipe_id: number; name?: string; category?: string }[];
  unchanged: number[];
  failed: { recipe_id: number; name?: string; error: string }[];
};
type BulkProgress = {
  current: number;
  total: number;
  recipeId: number;
  recipeName: string;
};

const MAX_SELECTION = 100;

function parseTags(value: string) {
  const seen = new Set<string>();
  return value
    .split(/[,;\n]/)
    .map(tag => tag.trim().replace(/\s+/g, ' '))
    .filter(tag => {
      const key = tag.toLocaleLowerCase('de-DE');
      if (!tag || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function appendTag(value: string, tag: string) {
  const tags = parseTags(value);
  if (!tags.some(current => current.toLocaleLowerCase('de-DE') === tag.toLocaleLowerCase('de-DE'))) {
    tags.push(tag);
  }
  return tags.join(', ');
}

export function AdminBulkEditor({
  visible,
  onClose,
  onChanged,
}: {
  visible: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [facets, setFacets] = useState<RecipeFacets | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState('');
  const [stage, setStage] = useState<'select' | 'edit'>('select');
  const [category, setCategory] = useState('');
  const [addTags, setAddTags] = useState('');
  const [removeTags, setRemoveTags] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<BulkProgress | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    setStage('select');
    setSelected(new Set());
    setQuery('');
    setCategory('');
    setAddTags('');
    setRemoveTags('');
    setProgress(null);
    setError('');
    setLoading(true);
    void Promise.all([
      api<RecipeResponse>('/api/recipes?limit=500', {}, controller.signal),
      api<RecipeFacets>('/api/recipes/facets', {}, controller.signal),
    ]).then(([recipeResult, facetResult]) => {
      setRecipes(recipeResult.items);
      setFacets(facetResult);
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : 'Rezepte konnten nicht geladen werden.');
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [visible]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('de-DE');
    if (!needle) return recipes;
    return recipes.filter(recipe => (
      recipe.name.toLocaleLowerCase('de-DE').includes(needle)
      || (recipe.category || '').toLocaleLowerCase('de-DE').includes(needle)
      || (recipe.type || '').toLocaleLowerCase('de-DE').includes(needle)
    ));
  }, [query, recipes]);

  function toggleRecipe(recipeId: number) {
    setSelected(current => {
      const next = new Set(current);
      if (next.has(recipeId)) {
        next.delete(recipeId);
      } else if (next.size < MAX_SELECTION) {
        next.add(recipeId);
      } else {
        Alert.alert('Auswahl begrenzt', `Pro Durchgang können höchstens ${MAX_SELECTION} Rezepte geändert werden.`);
      }
      return next;
    });
  }

  function selectFiltered() {
    setSelected(new Set(filtered.slice(0, MAX_SELECTION).map(recipe => recipe.id)));
    if (filtered.length > MAX_SELECTION) {
      Alert.alert('Erste 100 ausgewählt', 'Für weitere Rezepte bitte einen zweiten Durchgang starten.');
    }
  }

  function requestApply() {
    const additions = parseTags(addTags);
    const removals = parseTags(removeTags);
    if (!category.trim() && !additions.length && !removals.length) {
      setError('Bitte eine Kategorie oder mindestens eine Tag-Änderung angeben.');
      return;
    }
    const overlap = additions.filter(tag => removals.some(removal => (
      removal.toLocaleLowerCase('de-DE') === tag.toLocaleLowerCase('de-DE')
    )));
    if (overlap.length) {
      setError(`Diese Tags stehen in beiden Feldern: ${overlap.join(', ')}`);
      return;
    }
    Alert.alert(
      `${selected.size} Rezepte ändern?`,
      'Vor jedem Rezept wird eine wiederherstellbare Version angelegt. Kategorieänderungen verschieben den zugehörigen Ordner.',
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Änderungen anwenden', onPress: () => void applyChanges(additions, removals) },
      ],
    );
  }

  async function applyChanges(additions: string[], removals: string[]) {
    setBusy(true);
    setError('');
    const recipeById = new Map(recipes.map(recipe => [recipe.id, recipe]));
    const targets = [...selected].map(recipeId => ({
      id: recipeId,
      name: recipeById.get(recipeId)?.name || `Rezept #${recipeId}`,
    }));
    const result: BulkResult = { ok: true, updated: [], unchanged: [], failed: [] };
    try {
      for (const [index, recipe] of targets.entries()) {
        setProgress({
          current: index + 1,
          total: targets.length,
          recipeId: recipe.id,
          recipeName: recipe.name,
        });
        try {
          const itemResult = await api<BulkResult>('/api/recipes/bulk-edit', {
            method: 'POST',
            body: JSON.stringify({
              recipe_ids: [recipe.id],
              category: category.trim() || null,
              add_tags: additions,
              remove_tags: removals,
            }),
          });
          result.updated.push(...itemResult.updated);
          result.unchanged.push(...itemResult.unchanged);
          result.failed.push(...itemResult.failed);
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : 'Serverfehler';
          setSelected(new Set(targets.slice(index).map(item => item.id)));
          setError(
            `Unterbrochen bei „${recipe.name}“: ${message}. `
            + `${result.updated.length} von ${targets.length} Rezepten wurden geändert.`,
          );
          if (result.updated.length) onChanged();
          return;
        }
      }
      if (result.updated.length) onChanged();
      if (!result.failed.length) {
        Alert.alert(
          'Massenpflege abgeschlossen',
          `${result.updated.length} geändert${result.unchanged.length ? ` · ${result.unchanged.length} bereits passend` : ''}.`,
        );
        onClose();
        return;
      }
      setSelected(new Set(result.failed.map(item => item.recipe_id)));
      setStage('select');
      Alert.alert(
        'Teilweise abgeschlossen',
        `${result.updated.length} geändert, ${result.failed.length} fehlgeschlagen.\n\n${result.failed.slice(0, 3).map(item => `${item.name || `#${item.recipe_id}`}: ${item.error}`).join('\n')}`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Massenpflege fehlgeschlagen.');
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }

  const close = () => {
    if (!busy) onClose();
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={close}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.header}>
            <Pressable accessibilityRole="button" disabled={busy} onPress={stage === 'edit' ? () => setStage('select') : close} style={styles.headerAction}>
              <Text style={styles.headerActionText}>{stage === 'edit' ? 'Zurück' : 'Abbrechen'}</Text>
            </Pressable>
            <Text style={styles.title}>{stage === 'edit' ? 'Änderungen' : 'Massenpflege'}</Text>
            <View style={styles.headerAction} />
          </View>

          {loading ? (
            <StateView title="Rezepte werden geladen" loading />
          ) : stage === 'select' ? (
            <View style={styles.selectStage}>
              <View style={styles.controls}>
                <TextInput
                  autoCapitalize="none"
                  autoCorrect={false}
                  placeholder="Rezepte oder Kategorien suchen"
                  placeholderTextColor={colors.muted}
                  value={query}
                  onChangeText={setQuery}
                  style={sharedStyles.input}
                />
                <View style={styles.selectionActions}>
                  <Pressable accessibilityRole="button" onPress={selectFiltered} style={styles.textButton}><Text style={styles.textButtonLabel}>Treffer auswählen</Text></Pressable>
                  <Pressable accessibilityRole="button" onPress={() => setSelected(new Set())} style={styles.textButton}><Text style={styles.textButtonLabel}>Auswahl leeren</Text></Pressable>
                </View>
                <Text style={styles.selectionSummary}>{selected.size} von maximal {MAX_SELECTION} ausgewählt</Text>
                {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
              </View>
              <FlatList
                data={filtered}
                keyExtractor={item => String(item.id)}
                contentContainerStyle={styles.list}
                keyboardShouldPersistTaps="handled"
                renderItem={({ item }) => {
                  const checked = selected.has(item.id);
                  return (
                    <Pressable
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked }}
                      onPress={() => toggleRecipe(item.id)}
                      style={({ pressed }) => [styles.recipeRow, checked && styles.recipeRowSelected, pressed && styles.pressed]}>
                      <View style={[styles.checkbox, checked && styles.checkboxSelected]}>
                        {checked && <SymbolView name="checkmark" size={15} weight="bold" tintColor={colors.text} />}
                      </View>
                      <View style={styles.recipeText}>
                        <Text numberOfLines={1} style={styles.recipeName}>{item.name}</Text>
                        <Text numberOfLines={1} style={styles.recipeMeta}>{[item.type, item.category].filter(Boolean).join(' · ')}</Text>
                      </View>
                    </Pressable>
                  );
                }}
                ListEmptyComponent={<Text style={styles.empty}>Keine passenden Rezepte gefunden.</Text>}
              />
              <View style={styles.bottomBar}>
                <PrimaryButton label={selected.size ? `${selected.size} Rezepte bearbeiten` : 'Rezepte auswählen'} onPress={() => { setError(''); setStage('edit'); }} disabled={!selected.size} />
              </View>
            </View>
          ) : (
            <ScrollView contentContainerStyle={styles.editContent} keyboardShouldPersistTaps="handled">
              <Text style={styles.intro}>{selected.size} ausgewählte Rezepte. Leere Felder bleiben unverändert.</Text>
              <View style={sharedStyles.card}>
                <Text style={styles.label}>Neue Kategorie</Text>
                <TextInput
                  autoCapitalize="words"
                  editable={!busy}
                  maxLength={200}
                  placeholder="Kategorie beibehalten"
                  placeholderTextColor={colors.muted}
                  value={category}
                  onChangeText={setCategory}
                  style={sharedStyles.input}
                />
                {!!facets?.categories.length && (
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                    {facets.categories.map(value => (
                      <Pressable
                        key={value}
                        accessibilityRole="button"
                        accessibilityState={{ selected: category === value }}
                        onPress={() => setCategory(value)}
                        style={({ pressed }) => [styles.chip, category === value && styles.chipSelected, pressed && styles.pressed]}>
                        <Text style={styles.chipText}>{value}</Text>
                      </Pressable>
                    ))}
                  </ScrollView>
                )}
              </View>
              <View style={sharedStyles.card}>
                <Text style={styles.label}>Tags hinzufügen</Text>
                <TextInput
                  autoCapitalize="words"
                  editable={!busy}
                  maxLength={1000}
                  multiline
                  placeholder="z. B. Feierabend, Familie"
                  placeholderTextColor={colors.muted}
                  value={addTags}
                  onChangeText={setAddTags}
                  style={[sharedStyles.input, styles.tagInput]}
                />
                {!!facets?.tags.length && (
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                    {facets.tags.slice(0, 15).map(tag => (
                      <Pressable
                        key={tag.id}
                        accessibilityLabel={`${tag.name} hinzufügen`}
                        accessibilityRole="button"
                        onPress={() => setAddTags(value => appendTag(value, tag.name))}
                        style={({ pressed }) => [styles.chip, pressed && styles.pressed]}>
                        <Text style={styles.chipText}>+ {tag.name}</Text>
                      </Pressable>
                    ))}
                  </ScrollView>
                )}
                <Text style={styles.help}>Mehrere Tags mit Komma trennen.</Text>
              </View>
              <View style={sharedStyles.card}>
                <Text style={styles.label}>Eigene Tags entfernen</Text>
                <TextInput
                  autoCapitalize="words"
                  editable={!busy}
                  maxLength={1000}
                  multiline
                  placeholder="z. B. Alt, Test"
                  placeholderTextColor={colors.muted}
                  value={removeTags}
                  onChangeText={setRemoveTags}
                  style={[sharedStyles.input, styles.tagInput]}
                />
                <Text style={styles.help}>Automatisch erkannte Tags werden nicht entfernt.</Text>
              </View>
              {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
              {busy && progress && (
                <View
                  accessible
                  accessibilityLabel={`Rezept ${progress.current} von ${progress.total}: ${progress.recipeName}`}
                  accessibilityLiveRegion="polite"
                  accessibilityRole="progressbar"
                  accessibilityValue={{
                    min: 0,
                    max: progress.total,
                    now: progress.current - 1,
                    text: `${progress.recipeName} wird bearbeitet`,
                  }}
                  style={styles.progressCard}>
                  <ActivityIndicator color={colors.text} />
                  <View style={styles.progressText}>
                    <Text style={styles.progressCount}>Rezept {progress.current} von {progress.total}</Text>
                    <Text numberOfLines={2} style={styles.progressName}>{progress.recipeName}</Text>
                    <Text style={styles.progressHint}>Wird gerade bearbeitet …</Text>
                  </View>
                </View>
              )}
              <PrimaryButton label={busy ? 'Änderungen laufen …' : 'Änderungen prüfen'} onPress={requestApply} disabled={busy} />
            </ScrollView>
          )}
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { width: 82, minHeight: 44, justifyContent: 'center' },
  headerActionText: { color: colors.text, fontSize: 15, fontWeight: '700' },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  selectStage: { flex: 1 },
  controls: { padding: space.md, paddingBottom: space.sm, gap: space.sm },
  selectionActions: { flexDirection: 'row', justifyContent: 'space-between' },
  textButton: { minHeight: 44, justifyContent: 'center' },
  textButtonLabel: { color: colors.text, fontWeight: '800' },
  selectionSummary: { color: colors.muted, fontSize: 13 },
  list: { paddingHorizontal: space.md, paddingBottom: 100, gap: 8 },
  recipeRow: { minHeight: 64, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 12, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  recipeRowSelected: { borderColor: colors.butterPressed, backgroundColor: '#FFF4CE' },
  checkbox: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: colors.border, borderRadius: 9, backgroundColor: colors.white },
  checkboxSelected: { borderColor: colors.butterPressed, backgroundColor: colors.butter },
  recipeText: { flex: 1, gap: 3 },
  recipeName: { color: colors.text, fontSize: 16, fontWeight: '800' },
  recipeMeta: { color: colors.muted, fontSize: 13 },
  bottomBar: { padding: space.md, paddingTop: space.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, backgroundColor: colors.cream },
  editContent: { padding: space.md, paddingBottom: 50, gap: space.md },
  intro: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  label: { color: colors.text, fontSize: 15, fontWeight: '900' },
  chipRow: { gap: 8, paddingRight: space.md },
  chip: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderWidth: 1, borderColor: colors.border, borderRadius: 22, backgroundColor: colors.surface },
  chipSelected: { borderColor: colors.butterPressed, backgroundColor: colors.butter },
  chipText: { color: colors.text, fontSize: 14, fontWeight: '700' },
  tagInput: { minHeight: 72, paddingTop: 13, textAlignVertical: 'top' },
  help: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  progressCard: { minHeight: 88, padding: space.md, flexDirection: 'row', alignItems: 'center', gap: 14, borderWidth: 1, borderColor: colors.butterPressed, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  progressText: { flex: 1, gap: 3 },
  progressCount: { color: colors.muted, fontSize: 12, fontWeight: '800' },
  progressName: { color: colors.text, fontSize: 17, lineHeight: 22, fontWeight: '900' },
  progressHint: { color: colors.warning, fontSize: 13, fontWeight: '700' },
  empty: { color: colors.muted, padding: space.lg, textAlign: 'center' },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
  pressed: { opacity: 0.7 },
});
