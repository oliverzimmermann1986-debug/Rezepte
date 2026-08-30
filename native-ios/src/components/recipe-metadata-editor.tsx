import React, { useEffect, useState } from 'react';
import { SymbolView } from 'expo-symbols';
import {
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

import { PrimaryButton, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { invalidateApiCache, invalidateApiCacheByPrefix } from '@/lib/cache';
import { optionalInteger } from '@/lib/numbers';
import { RecipeDetail } from '@/lib/types';

type RecipeFacets = {
  categories: string[];
  tags: { id: number; name: string }[];
};

const MAX_USER_TAGS = 30;
const MAX_TAG_LENGTH = 80;

function normalizedTag(value: string) {
  return value.trim().replace(/\s+/g, ' ');
}

function tagKey(value: string) {
  return normalizedTag(value).toLocaleLowerCase('de-DE');
}

function withTag(current: string[], value: string, automaticTags: string[]) {
  const tag = normalizedTag(value);
  if (!tag) return current;
  if (tag.length > MAX_TAG_LENGTH) {
    throw new Error(`Ein Tag darf höchstens ${MAX_TAG_LENGTH} Zeichen lang sein.`);
  }
  const key = tagKey(tag);
  if (current.some(existing => tagKey(existing) === key)
    || automaticTags.some(existing => tagKey(existing) === key)) return current;
  if (current.length >= MAX_USER_TAGS) {
    throw new Error(`Pro Rezept sind höchstens ${MAX_USER_TAGS} eigene Tags möglich.`);
  }
  return [...current, tag];
}

function sameTags(left: string[], right: string[]) {
  const sortedKeys = (values: string[]) => JSON.stringify(values.map(tagKey).sort());
  return sortedKeys(left) === sortedKeys(right);
}

export function RecipeMetadataEditor({
  recipe,
  visible,
  onClose,
  onSaved,
}: {
  recipe: RecipeDetail;
  visible: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [recipeType, setRecipeType] = useState('');
  const [category, setCategory] = useState('');
  const [servings, setServings] = useState('');
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState('');
  const [categorySuggestions, setCategorySuggestions] = useState<string[]>([]);
  const [tagSuggestions, setTagSuggestions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    setName(recipe.name || '');
    setRecipeType(recipe.type || 'Sonstiges');
    setCategory(recipe.category || 'Allgemein');
    setServings(recipe.servings ? String(recipe.servings) : '');
    setUrl(recipe.url || '');
    setDescription(recipe.description || '');
    setTags(recipe.tags.filter(tag => !tag.auto).map(tag => tag.name));
    setTagDraft('');
    setCategorySuggestions([]);
    setTagSuggestions([]);
    setError('');
    void api<RecipeFacets>('/api/recipes/facets', {}, controller.signal)
      .then(result => {
        setCategorySuggestions(result.categories);
        setTagSuggestions(result.tags.map(tag => tag.name));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [recipe, visible]);

  const automaticTags = recipe.tags.filter(tag => Boolean(tag.auto)).map(tag => tag.name);
  const availableTagSuggestions = tagSuggestions.filter(suggestion => {
    const key = tagKey(suggestion);
    return !tags.some(tag => tagKey(tag) === key)
      && !automaticTags.some(tag => tagKey(tag) === key);
  }).slice(0, 12);

  function addTag(value = tagDraft) {
    try {
      setTags(withTag(tags, value, automaticTags));
      setTagDraft('');
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Tag konnte nicht hinzugefügt werden.');
    }
  }

  async function save() {
    setBusy(true);
    setError('');
    let metadataSaved = false;
    let serverChanged = false;
    let failure: unknown = null;
    try {
      try {
        if (!name.trim() || !recipeType.trim() || !category.trim()) {
          throw new Error('Name, Typ und Kategorie dürfen nicht leer sein.');
        }
        const nextServings = optionalInteger(servings, 'Portionen', 1, 50);
        const nextTags = withTag(tags, tagDraft, automaticTags);
        const nextUrl = url.trim() || null;
        const metadataChanged = name.trim() !== (recipe.name || '').trim()
          || recipeType.trim() !== (recipe.type || 'Sonstiges').trim()
          || category.trim() !== (recipe.category || 'Allgemein').trim()
          || nextServings !== (recipe.servings ?? null)
          || nextUrl !== ((recipe.url || '').trim() || null)
          || description.trim() !== (recipe.description || '').trim();
        if (metadataChanged) {
          await api(`/api/recipes/${recipe.id}/metadata`, {
            method: 'PUT',
            body: JSON.stringify({
              name: name.trim(),
              type: recipeType.trim(),
              category: category.trim(),
              servings: nextServings,
              url: nextUrl,
              description: description.trim(),
            }),
          });
          metadataSaved = true;
          serverChanged = true;
        }
        const originalTags = recipe.tags.filter(tag => !tag.auto).map(tag => tag.name);
        if (!sameTags(nextTags, originalTags)) {
          await api(`/api/recipes/${recipe.id}/tags`, {
            method: 'PUT',
            body: JSON.stringify({ tags: nextTags }),
          });
          serverChanged = true;
        }
      } catch (reason) {
        failure = reason;
      }

      // Cachepflege folgt erst, nachdem alle vorgesehenen Servermutationen
      // versucht wurden. Die Helfer sind best effort und können den Save nicht
      // nachträglich in einen Fehler verwandeln.
      if (serverChanged) {
        await Promise.all([
          invalidateApiCache(`recipe:${recipe.id}`),
          invalidateApiCacheByPrefix('recipes:'),
        ]);
      }
      if (failure) {
        const message = failure instanceof Error ? failure.message : 'Speichern fehlgeschlagen';
        setError(metadataSaved
          ? `Name, Kategorie und weitere Angaben wurden gespeichert. Tags konnten nicht gespeichert werden: ${message}`
          : message);
        return;
      }
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.header}>
            <Pressable disabled={busy} onPress={onClose} style={styles.headerAction}><Text style={styles.cancel}>Abbrechen</Text></Pressable>
            <Text style={styles.title}>Rezept bearbeiten</Text>
            <View style={styles.headerAction} />
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <View style={sharedStyles.card}>
              <Text style={styles.label}>Name</Text>
              <TextInput autoCapitalize="sentences" placeholder="Rezeptname" placeholderTextColor={colors.muted} value={name} onChangeText={setName} style={sharedStyles.input} />
              <View style={styles.columns}>
                <View style={styles.flex}>
                  <Text style={styles.label}>Typ</Text>
                  <TextInput placeholder="z. B. Hauptgericht" placeholderTextColor={colors.muted} value={recipeType} onChangeText={setRecipeType} style={sharedStyles.input} />
                </View>
                <View style={styles.flex}>
                  <Text style={styles.label}>Kategorie</Text>
                  <TextInput placeholder="z. B. Pasta" placeholderTextColor={colors.muted} value={category} onChangeText={setCategory} style={sharedStyles.input} />
                </View>
              </View>
              {!!categorySuggestions.length && (
                <>
                  <Text style={styles.suggestionLabel}>Vorhandene Kategorien</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.suggestionRow}>
                    {categorySuggestions.map(value => {
                      const selected = category.trim() === value;
                      return (
                        <Pressable
                          key={value}
                          accessibilityRole="button"
                          accessibilityState={{ selected }}
                          onPress={() => setCategory(value)}
                          style={({ pressed }) => [styles.suggestionChip, selected && styles.suggestionChipSelected, pressed && styles.pressed]}>
                          <Text style={[styles.suggestionText, selected && styles.suggestionTextSelected]}>{value}</Text>
                        </Pressable>
                      );
                    })}
                  </ScrollView>
                </>
              )}
              <Text style={styles.label}>Portionen</Text>
              <TextInput keyboardType="number-pad" placeholder="Optional" placeholderTextColor={colors.muted} value={servings} onChangeText={setServings} style={sharedStyles.input} />
              <Text style={styles.help}>Name, Typ und Kategorie bestimmen auch den sicheren Ablageort des Rezepts.</Text>
            </View>
            <View style={sharedStyles.card}>
              <Text style={styles.label}>Eigene Tags</Text>
              <Text style={styles.help}>Zum Entfernen auf ein Tag tippen.</Text>
              {!!tags.length ? (
                <View style={styles.tagWrap}>
                  {tags.map(tag => (
                    <Pressable
                      key={tagKey(tag)}
                      accessibilityLabel={`${tag} entfernen`}
                      accessibilityRole="button"
                      disabled={busy}
                      onPress={() => setTags(current => current.filter(value => tagKey(value) !== tagKey(tag)))}
                      style={({ pressed }) => [styles.tagChip, pressed && styles.pressed]}>
                      <Text style={styles.tagText}>{tag}</Text>
                      <SymbolView name="xmark" size={12} weight="bold" tintColor={colors.text} />
                    </Pressable>
                  ))}
                </View>
              ) : (
                <Text style={styles.emptyTags}>Noch keine eigenen Tags.</Text>
              )}
              <View style={styles.tagInputRow}>
                <TextInput
                  autoCapitalize="words"
                  blurOnSubmit={false}
                  editable={!busy}
                  maxLength={MAX_TAG_LENGTH}
                  onChangeText={setTagDraft}
                  onSubmitEditing={() => addTag()}
                  placeholder="z. B. Feierabend"
                  placeholderTextColor={colors.muted}
                  returnKeyType="done"
                  style={[sharedStyles.input, styles.tagInput]}
                  value={tagDraft}
                />
                <Pressable
                  accessibilityLabel="Tag hinzufügen"
                  accessibilityRole="button"
                  disabled={busy || !tagDraft.trim()}
                  onPress={() => addTag()}
                  style={({ pressed }) => [styles.addTagButton, (!tagDraft.trim() || busy) && styles.disabled, pressed && styles.pressed]}>
                  <SymbolView name="plus" size={18} weight="bold" tintColor={colors.text} />
                </Pressable>
              </View>
              {!!availableTagSuggestions.length && (
                <>
                  <Text style={styles.suggestionLabel}>Vorschläge</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.suggestionRow}>
                    {availableTagSuggestions.map(tag => (
                      <Pressable
                        key={tagKey(tag)}
                        accessibilityLabel={`${tag} hinzufügen`}
                        accessibilityRole="button"
                        disabled={busy}
                        onPress={() => addTag(tag)}
                        style={({ pressed }) => [styles.suggestionChip, pressed && styles.pressed]}>
                        <Text style={styles.suggestionText}>+ {tag}</Text>
                      </Pressable>
                    ))}
                  </ScrollView>
                </>
              )}
              {!!automaticTags.length && (
                <>
                  <Text style={styles.suggestionLabel}>Automatisch erkannt</Text>
                  <View style={styles.tagWrap}>
                    {automaticTags.map(tag => <View key={tagKey(tag)} style={styles.autoTag}><Text style={styles.autoTagText}>{tag}</Text></View>)}
                  </View>
                  <Text style={styles.help}>Automatische Tags werden aus dem Rezept ermittelt und bleiben unverändert.</Text>
                </>
              )}
            </View>
            <View style={sharedStyles.card}>
              <Text style={styles.label}>Rezept-Webseite oder Quell-Link</Text>
              <TextInput autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="https://…" placeholderTextColor={colors.muted} value={url} onChangeText={setUrl} style={sharedStyles.input} />
              <Text style={styles.help}>Der Link wird ausschließlich in der jeweiligen Plattform geöffnet.</Text>
            </View>
            <View style={sharedStyles.card}>
              <Text style={styles.label}>Beschreibung</Text>
              <TextInput multiline placeholder="Beschreibung oder Hinweise" placeholderTextColor={colors.muted} value={description} onChangeText={setDescription} style={[sharedStyles.input, styles.description]} />
            </View>
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
            <PrimaryButton label={busy ? 'Speichert …' : 'Änderungen speichern'} onPress={save} disabled={busy} />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { width: 82, minHeight: 44, justifyContent: 'center' },
  cancel: { color: colors.text, fontSize: 15, fontWeight: '700' },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  content: { padding: space.md, paddingBottom: 50, gap: space.md },
  label: { color: colors.text, fontSize: 14, fontWeight: '900' },
  columns: { flexDirection: 'row', gap: 8 },
  flex: { flex: 1, gap: 8 },
  suggestionLabel: { color: colors.muted, fontSize: 12, fontWeight: '800', marginTop: 2 },
  suggestionRow: { gap: 8, paddingRight: space.md },
  suggestionChip: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 22, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  suggestionChipSelected: { backgroundColor: colors.butter, borderColor: colors.butterPressed },
  suggestionText: { color: colors.text, fontSize: 14, fontWeight: '700' },
  suggestionTextSelected: { fontWeight: '900' },
  tagWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tagChip: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, borderRadius: 22, backgroundColor: colors.butter },
  tagText: { color: colors.text, fontSize: 14, fontWeight: '800' },
  tagInputRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  tagInput: { flex: 1 },
  addTagButton: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center', borderRadius: 24, backgroundColor: colors.butter },
  autoTag: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 18, borderWidth: 1, borderColor: colors.border },
  autoTagText: { color: colors.muted, fontSize: 13, fontWeight: '700' },
  emptyTags: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  disabled: { opacity: 0.45 },
  pressed: { opacity: 0.68 },
  description: { minHeight: 150, paddingTop: 12, textAlignVertical: 'top' },
  help: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
});
