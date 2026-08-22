import React, { useEffect, useState } from 'react';
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
import { optionalInteger } from '@/lib/numbers';
import { RecipeDetail } from '@/lib/types';

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    setName(recipe.name || '');
    setRecipeType(recipe.type || 'Sonstiges');
    setCategory(recipe.category || 'Allgemein');
    setServings(recipe.servings ? String(recipe.servings) : '');
    setUrl(recipe.url || '');
    setDescription(recipe.description || '');
    setError('');
  }, [recipe, visible]);

  async function save() {
    setBusy(true);
    setError('');
    try {
      if (!name.trim() || !recipeType.trim() || !category.trim()) {
        throw new Error('Name, Typ und Kategorie dürfen nicht leer sein.');
      }
      await api(`/api/recipes/${recipe.id}/metadata`, {
        method: 'PUT',
        body: JSON.stringify({
          name: name.trim(),
          type: recipeType.trim(),
          category: category.trim(),
          servings: optionalInteger(servings, 'Portionen', 1, 50),
          url: url.trim() || null,
          description: description.trim(),
        }),
      });
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Speichern fehlgeschlagen');
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
              <Text style={styles.label}>Portionen</Text>
              <TextInput keyboardType="number-pad" placeholder="Optional" placeholderTextColor={colors.muted} value={servings} onChangeText={setServings} style={sharedStyles.input} />
            </View>
            <View style={sharedStyles.card}>
              <Text style={styles.label}>TikTok-, Instagram- oder Quell-Link</Text>
              <TextInput autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="https://…" placeholderTextColor={colors.muted} value={url} onChangeText={setUrl} style={sharedStyles.input} />
              <Text style={styles.help}>Der Link wird nur extern geöffnet. Die App lädt oder zeigt kein Social-Media-Video.</Text>
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
  description: { minHeight: 150, paddingTop: 12, textAlignVertical: 'top' },
  help: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
});
