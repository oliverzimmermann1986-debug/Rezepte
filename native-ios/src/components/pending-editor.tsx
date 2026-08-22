import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Linking,
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
import { optionalInteger, optionalNumber } from '@/lib/numbers';
import { Ingredient, PendingItem, RecipeStep } from '@/lib/types';

type EditableIngredient = Omit<Ingredient, 'amount'> & { amount?: number | string | null };
type EditableStep = Omit<RecipeStep, 'timer_seconds'> & { timer_seconds?: number | string | null };

type Props = {
  item: PendingItem | null;
  onClose: () => void;
  onSaved: () => void;
};

export function PendingEditor({ item, onClose, onSaved }: Props) {
  const [name, setName] = useState('');
  const [recipeType, setRecipeType] = useState('Hauptgericht');
  const [category, setCategory] = useState('Allgemein');
  const [description, setDescription] = useState('');
  const [servings, setServings] = useState('');
  const [ingredients, setIngredients] = useState<EditableIngredient[]>([]);
  const [steps, setSteps] = useState<EditableStep[]>([]);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!item) return;
    const suggestion = item.ai_suggestion || {};
    setName(suggestion.name?.trim() || suggestion.filename?.replace(/\.[^.]+$/, '') || '');
    setRecipeType(suggestion.type?.trim() || 'Hauptgericht');
    setCategory(suggestion.category?.trim() || 'Allgemein');
    setDescription(item.description || '');
    setServings(suggestion.servings ? String(suggestion.servings) : '');
    setIngredients(suggestion.ingredients?.length ? suggestion.ingredients.map(value => ({ ...value, amount: value.amount == null ? '' : String(value.amount) })) : [{ name: '' }]);
    setSteps(suggestion.steps?.length ? suggestion.steps.map(value => ({ ...value, timer_seconds: value.timer_seconds == null ? '' : String(value.timer_seconds) })) : [{ instruction: '' }]);
    setVerified(false);
    setError('');
  }, [item]);

  async function resolve(action: 'save' | 'skip') {
    if (!item) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<{ ok: boolean; error?: string }>('/api/pending', {
        method: 'POST',
        body: JSON.stringify({
          url: item.url,
          action,
          name: name.trim(),
          type: recipeType.trim(),
          category: category.trim(),
          description: description.trim(),
          servings: optionalInteger(servings, 'Portionen', 1, 50),
          verified: verified && ingredients.some(value => value.name.trim()),
          ingredients: ingredients
            .filter(value => value.name.trim())
            .map(value => ({
              name: value.name.trim(),
              amount: optionalNumber(value.amount, `Menge für ${value.name}`),
              unit: value.unit?.trim() || null,
              raw: value.raw || null,
            })),
          steps: steps
            .filter(value => value.instruction.trim())
            .map(value => ({
              instruction: value.instruction.trim(),
              timer_seconds: optionalInteger(value.timer_seconds, 'Timer'),
            })),
        }),
      });
      if (!result.ok) throw new Error(result.error || 'Import konnte nicht gespeichert werden');
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Speichern fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  const externalSource = item?.url && /^https:\/\//i.test(item.url) ? item.url : null;
  const hasIngredients = ingredients.some(value => value.name.trim());

  return (
    <Modal visible={item !== null} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe}>
        <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.header}>
            <Pressable onPress={onClose} hitSlop={10}><Text style={styles.cancel}>Abbrechen</Text></Pressable>
            <Text style={styles.title}>Import prüfen</Text>
            <View style={{ width: 78 }} />
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <View style={sharedStyles.card}>
              <Text style={sharedStyles.sectionTitle}>Zuordnung</Text>
              <TextInput placeholder="Rezeptname" placeholderTextColor={colors.muted} value={name} onChangeText={setName} style={sharedStyles.input} />
              <View style={styles.twoColumns}>
                <TextInput placeholder="Typ" placeholderTextColor={colors.muted} value={recipeType} onChangeText={setRecipeType} style={[sharedStyles.input, styles.flex]} />
                <TextInput placeholder="Kategorie" placeholderTextColor={colors.muted} value={category} onChangeText={setCategory} style={[sharedStyles.input, styles.flex]} />
              </View>
              <TextInput placeholder="Portionen" placeholderTextColor={colors.muted} keyboardType="number-pad" value={servings} onChangeText={setServings} style={sharedStyles.input} />
              <TextInput multiline placeholder="Erkannter Text" placeholderTextColor={colors.muted} value={description} onChangeText={setDescription} style={[sharedStyles.input, styles.description]} />
            </View>

            <View style={styles.section}>
              <Text style={sharedStyles.sectionTitle}>Zutaten</Text>
              {ingredients.map((ingredient, index) => (
                <View key={index} style={styles.rowCard}>
                  <TextInput
                    placeholder={`Zutat ${index + 1}`}
                    placeholderTextColor={colors.muted}
                    value={ingredient.name}
                    onChangeText={value => setIngredients(rows => rows.map((row, i) => i === index ? { ...row, name: value } : row))}
                    style={sharedStyles.input}
                  />
                  <View style={styles.twoColumns}>
                    <TextInput
                      placeholder="Menge"
                      placeholderTextColor={colors.muted}
                      keyboardType="decimal-pad"
                      value={ingredient.amount == null ? '' : String(ingredient.amount)}
                      onChangeText={value => setIngredients(rows => rows.map((row, i) => i === index ? { ...row, amount: value } : row))}
                      style={[sharedStyles.input, styles.flex]}
                    />
                    <TextInput
                      placeholder="Einheit"
                      placeholderTextColor={colors.muted}
                      value={ingredient.unit || ''}
                      onChangeText={value => setIngredients(rows => rows.map((row, i) => i === index ? { ...row, unit: value } : row))}
                      style={[sharedStyles.input, styles.flex]}
                    />
                  </View>
                  <Pressable onPress={() => setIngredients(rows => rows.filter((_, i) => i !== index))}><Text style={styles.remove}>Entfernen</Text></Pressable>
                </View>
              ))}
              <PrimaryButton label="+ Zutat" onPress={() => setIngredients(rows => [...rows, { name: '' }])} />
            </View>

            <View style={styles.section}>
              <Text style={sharedStyles.sectionTitle}>Zubereitung</Text>
              {steps.map((step, index) => (
                <View key={index} style={styles.rowCard}>
                  <TextInput
                    multiline
                    placeholder={`Schritt ${index + 1}`}
                    placeholderTextColor={colors.muted}
                    value={step.instruction}
                    onChangeText={value => setSteps(rows => rows.map((row, i) => i === index ? { ...row, instruction: value } : row))}
                    style={[sharedStyles.input, styles.stepInput]}
                  />
                  <TextInput
                    placeholder="Timer in Sekunden"
                    placeholderTextColor={colors.muted}
                    keyboardType="number-pad"
                    value={step.timer_seconds == null ? '' : String(step.timer_seconds)}
                    onChangeText={value => setSteps(rows => rows.map((row, i) => i === index ? { ...row, timer_seconds: value } : row))}
                    style={sharedStyles.input}
                  />
                  <Pressable onPress={() => setSteps(rows => rows.filter((_, i) => i !== index))}><Text style={styles.remove}>Entfernen</Text></Pressable>
                </View>
              ))}
              <PrimaryButton label="+ Schritt" onPress={() => setSteps(rows => [...rows, { instruction: '' }])} />
            </View>

            <Pressable
              accessibilityRole="checkbox"
              accessibilityState={{ checked: verified }}
              disabled={!hasIngredients}
              onPress={() => setVerified(value => !value)}
              style={[styles.verifyRow, verified && styles.verifyRowActive, !hasIngredients && styles.disabled]}>
              <Text style={styles.checkbox}>{verified ? '✓' : ''}</Text>
              <View style={styles.flex}>
                <Text style={styles.verifyTitle}>Zutaten geprüft</Text>
                <Text style={styles.verifyHelp}>Bestätigt ausschließlich die kontrollierte Zutatenliste.</Text>
              </View>
            </Pressable>

            {!!externalSource && (
              <Pressable accessibilityRole="link" onPress={() => Linking.openURL(externalSource)}>
                <Text style={styles.source} numberOfLines={2}>Original bei {item?.ai_suggestion?.platform || 'der Plattform'} öffnen ↗</Text>
              </Pressable>
            )}
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
            <PrimaryButton label={busy ? 'Speichert …' : 'Rezept speichern'} onPress={() => resolve('save')} disabled={busy || !name.trim()} />
            <PrimaryButton label="Import verwerfen" onPress={() => resolve('skip')} disabled={busy} destructive />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 54, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  cancel: { color: colors.text, fontSize: 16, minWidth: 78 },
  title: { color: colors.text, fontSize: 17, fontWeight: '800' },
  content: { padding: space.md, paddingBottom: 48, gap: space.md },
  section: { gap: 10 },
  twoColumns: { flexDirection: 'row', gap: 8 },
  flex: { flex: 1 },
  description: { minHeight: 120, paddingTop: 12, textAlignVertical: 'top' },
  rowCard: { gap: 8, padding: 10, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  stepInput: { minHeight: 92, paddingTop: 12, textAlignVertical: 'top' },
  remove: { color: colors.danger, minHeight: 30, paddingTop: 5, fontWeight: '700' },
  verifyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  verifyRowActive: { borderColor: colors.success, backgroundColor: '#EAF6EE' },
  checkbox: { width: 28, height: 28, paddingTop: 3, borderWidth: 2, borderColor: colors.success, borderRadius: 8, color: colors.success, fontWeight: '900', textAlign: 'center' },
  verifyTitle: { color: colors.text, fontWeight: '800' },
  verifyHelp: { color: colors.muted, fontSize: 13, marginTop: 2 },
  disabled: { opacity: 0.45 },
  source: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  error: { color: colors.danger, lineHeight: 20 },
});
