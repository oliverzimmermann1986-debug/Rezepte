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

import { PrimaryButton } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';
import { Ingredient, RecipeStep } from '@/lib/types';

type EditableIngredient = Omit<Ingredient, 'amount'> & { amount?: number | string | null };
type EditableStep = Omit<RecipeStep, 'timer_seconds'> & { timer_seconds?: number | string | null };

function optionalNumber(value: number | string | null | undefined, label: string) {
  if (value === '' || value == null) return null;
  const parsed = Number(String(value).replace(',', '.'));
  if (!Number.isFinite(parsed)) throw new Error(`${label} ist keine gültige Zahl.`);
  return parsed;
}

type Props = {
  recipeId: number;
  kind: 'ingredients' | 'steps';
  ingredients: Ingredient[];
  steps: RecipeStep[];
  visible: boolean;
  onClose: () => void;
  onSaved: () => void;
};

export function RecipeEditor({ recipeId, kind, ingredients, steps, visible, onClose, onSaved }: Props) {
  const [ingredientRows, setIngredientRows] = useState<EditableIngredient[]>([]);
  const [stepRows, setStepRows] = useState<EditableStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    setIngredientRows(ingredients.length ? ingredients.map(item => ({ ...item, amount: item.amount == null ? '' : String(item.amount) })) : [{ name: '' }]);
    setStepRows(steps.length ? steps.map(item => ({ ...item, timer_seconds: item.timer_seconds == null ? '' : String(item.timer_seconds) })) : [{ instruction: '' }]);
    setError('');
  }, [ingredients, steps, visible]);

  async function save() {
    setBusy(true);
    setError('');
    try {
      if (kind === 'ingredients') {
        const cleaned = ingredientRows
          .filter(item => item.name.trim())
          .map(item => ({
            name: item.name.trim(),
            amount: optionalNumber(item.amount, `Menge für ${item.name}`),
            unit: item.unit?.trim() || null,
            raw: item.raw || null,
          }));
        await api(`/api/recipes/${recipeId}/ingredients`, {
          method: 'PUT',
          body: JSON.stringify({ ingredients: cleaned }),
        });
      } else {
        const cleaned = stepRows
          .filter(item => item.instruction.trim())
          .map(item => ({
            instruction: item.instruction.trim(),
            timer_seconds: optionalNumber(item.timer_seconds, 'Timer'),
          }));
        await api(`/api/recipes/${recipeId}/steps`, {
          method: 'PUT',
          body: JSON.stringify({ steps: cleaned }),
        });
      }
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Speichern fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe}>
        <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.header}>
            <Pressable onPress={onClose} hitSlop={10}><Text style={styles.cancel}>Abbrechen</Text></Pressable>
            <Text style={styles.title}>{kind === 'ingredients' ? 'Zutaten' : 'Schritte'} bearbeiten</Text>
            <View style={{ width: 72 }} />
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            {kind === 'ingredients' ? (
              <>
                {ingredientRows.map((item, index) => (
                  <View key={index} style={styles.rowCard}>
                    <Text style={styles.number}>{index + 1}</Text>
                    <TextInput
                      placeholder="Zutat"
                      placeholderTextColor={colors.muted}
                      value={item.name}
                      onChangeText={name => setIngredientRows(rows => rows.map((row, i) => i === index ? { ...row, name } : row))}
                      style={[styles.input, styles.nameInput]}
                    />
                    <View style={styles.amountRow}>
                      <TextInput
                        placeholder="Menge"
                        placeholderTextColor={colors.muted}
                        keyboardType="decimal-pad"
                        value={item.amount == null ? '' : String(item.amount)}
                        onChangeText={value => setIngredientRows(rows => rows.map((row, i) => i === index ? { ...row, amount: value } : row))}
                        style={[styles.input, styles.flex]}
                      />
                      <TextInput
                        placeholder="Einheit"
                        placeholderTextColor={colors.muted}
                        value={item.unit || ''}
                        onChangeText={unit => setIngredientRows(rows => rows.map((row, i) => i === index ? { ...row, unit } : row))}
                        style={[styles.input, styles.flex]}
                      />
                    </View>
                    <Pressable
                      onPress={() => setIngredientRows(rows => rows.filter((_, i) => i !== index))}
                      hitSlop={8}>
                      <Text style={styles.remove}>Entfernen</Text>
                    </Pressable>
                  </View>
                ))}
                <PrimaryButton label="+ Zutat" onPress={() => setIngredientRows(rows => [...rows, { name: '' }])} />
              </>
            ) : (
              <>
                {stepRows.map((item, index) => (
                  <View key={index} style={styles.rowCard}>
                    <Text style={styles.number}>Schritt {index + 1}</Text>
                    <TextInput
                      multiline
                      placeholder="Zubereitung beschreiben"
                      placeholderTextColor={colors.muted}
                      value={item.instruction}
                      onChangeText={instruction => setStepRows(rows => rows.map((row, i) => i === index ? { ...row, instruction } : row))}
                      style={[styles.input, styles.multiline]}
                    />
                    <TextInput
                      placeholder="Timer in Sekunden (optional)"
                      placeholderTextColor={colors.muted}
                      keyboardType="number-pad"
                      value={item.timer_seconds == null ? '' : String(item.timer_seconds)}
                      onChangeText={value => setStepRows(rows => rows.map((row, i) => i === index ? { ...row, timer_seconds: value } : row))}
                      style={styles.input}
                    />
                    <Pressable onPress={() => setStepRows(rows => rows.filter((_, i) => i !== index))} hitSlop={8}>
                      <Text style={styles.remove}>Entfernen</Text>
                    </Pressable>
                  </View>
                ))}
                <PrimaryButton label="+ Schritt" onPress={() => setStepRows(rows => [...rows, { instruction: '' }])} />
              </>
            )}
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
            <PrimaryButton label={busy ? 'Speichern …' : 'Speichern'} onPress={save} disabled={busy} />
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: {
    minHeight: 54,
    paddingHorizontal: space.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  cancel: { color: colors.text, fontSize: 16, minWidth: 72 },
  title: { color: colors.text, fontSize: 16, fontWeight: '800' },
  content: { padding: space.md, paddingBottom: 48, gap: 12 },
  rowCard: {
    padding: 12,
    gap: 10,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  number: { color: colors.muted, fontSize: 13, fontWeight: '800' },
  input: {
    minHeight: 48,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.sm,
    backgroundColor: colors.white,
    color: colors.text,
    fontSize: 16,
  },
  nameInput: { fontWeight: '700' },
  multiline: { minHeight: 100, paddingTop: 12, textAlignVertical: 'top' },
  amountRow: { flexDirection: 'row', gap: 8 },
  flex: { flex: 1 },
  remove: { color: colors.danger, minHeight: 32, paddingTop: 6, fontWeight: '700' },
  error: { color: colors.danger, lineHeight: 20 },
});
