import React, { useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Image } from 'expo-image';
import * as Sharing from 'expo-sharing';

import { PrimaryButton, sharedStyles } from '@/components/ui';
import { UnitPicker } from '@/components/unit-picker';
import { colors, radii, space } from '@/constants/design';
import {
  absoluteApiUrl,
  ApiError,
  api,
  apiAuthHeaders,
  assertApiSessionEpochCurrent,
  currentApiSessionEpoch,
  deleteCachedFile,
  downloadFileToCache,
} from '@/lib/api';
import { invalidateApiCacheByPrefix } from '@/lib/cache';
import {
  createIngredientRow,
  createStepRow,
  EditableIngredient,
  EditableStep,
} from '@/lib/editor-rows';
import { normalizedExternalUrl, openExternalUrl } from '@/lib/external-links';
import { optionalInteger, optionalNumber } from '@/lib/numbers';
import { PendingItem } from '@/lib/types';
import { normalizeUnit } from '@/lib/units';

type Props = {
  item: PendingItem | null;
  onClose: () => void;
  onSaved: () => void;
};

type ReanalyzeResult = {
  ok: boolean;
  action?: 'auto_saved' | 'still_pending' | string;
  error?: string;
  message?: string;
  description?: string | null;
  analysis?: PendingItem['ai_suggestion'];
};

export function PendingEditor({ item, onClose, onSaved }: Props) {
  const { width, fontScale } = useWindowDimensions();
  const [name, setName] = useState('');
  const [recipeType, setRecipeType] = useState('Hauptgericht');
  const [category, setCategory] = useState('Allgemein');
  const [description, setDescription] = useState('');
  const [servings, setServings] = useState('');
  const [ingredients, setIngredients] = useState<EditableIngredient[]>([]);
  const [steps, setSteps] = useState<EditableStep[]>([]);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [error, setError] = useState('');
  const [previewUnavailable, setPreviewUnavailable] = useState(false);

  useEffect(() => {
    if (!item) return;
    const suggestion = item.ai_suggestion || {};
    setName(suggestion.name?.trim() || suggestion.filename?.replace(/\.[^.]+$/, '') || '');
    setRecipeType(suggestion.type?.trim() || 'Hauptgericht');
    setCategory(suggestion.category?.trim() || 'Allgemein');
    setDescription(item.description || '');
    setServings(suggestion.servings ? String(suggestion.servings) : '');
    setIngredients(suggestion.ingredients?.length ? suggestion.ingredients.map(createIngredientRow) : [createIngredientRow()]);
    setSteps(suggestion.steps?.length ? suggestion.steps.map(createStepRow) : [createStepRow()]);
    setVerified(false);
    setError('');
    setPreviewUnavailable(false);
  }, [item]);

  async function save() {
    if (!item) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<{ ok: boolean; error?: string }>('/api/pending', {
        method: 'POST',
        body: JSON.stringify({
          url: item.url,
          action: 'save',
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
              unit: normalizeUnit(value.unit) || null,
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
      await invalidateApiCacheByPrefix('recipe:', 'recipes:');
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Speichern fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  async function reanalyze() {
    if (!item || busy) return;
    setBusy(true);
    setAiBusy(true);
    setError('');
    try {
      const result = await api<ReanalyzeResult>(
        '/api/pending/reanalyze',
        {
          method: 'POST',
          body: JSON.stringify({ url: item.url }),
        },
        undefined,
        120_000,
      );
      if (!result.ok) throw new Error(result.error || 'KI-Prüfung fehlgeschlagen');

      if (result.action === 'auto_saved') {
        await invalidateApiCacheByPrefix('recipe:', 'recipes:');
        Alert.alert(
          'KI-Prüfung abgeschlossen',
          result.message || 'Das vollständige Rezept wurde automatisch einsortiert.',
        );
        onSaved();
        return;
      }

      const suggestion = result.analysis || {};
      if (suggestion.name?.trim()) setName(suggestion.name.trim());
      if (suggestion.type?.trim()) setRecipeType(suggestion.type.trim());
      if (suggestion.category?.trim()) setCategory(suggestion.category.trim());
      if (result.description?.trim()) setDescription(result.description.trim());
      if (suggestion.servings) setServings(String(suggestion.servings));
      if (suggestion.ingredients?.length) {
        setIngredients(suggestion.ingredients.map(createIngredientRow));
      }
      if (suggestion.steps?.length) {
        setSteps(suggestion.steps.map(createStepRow));
      }
      setVerified(false);
      Alert.alert(
        'KI-Vorschlag aktualisiert',
        result.message || 'Bitte Zutaten und Zubereitung kontrollieren und anschließend speichern.',
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'KI-Prüfung fehlgeschlagen');
    } finally {
      setAiBusy(false);
      setBusy(false);
    }
  }

  function requestDiscard() {
    if (!item || busy) return;
    Alert.alert(
      'Import wirklich verwerfen?',
      'Der Eingang wird als verworfen markiert und erscheint nicht mehr in der manuellen Prüfung.',
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Verwerfen', style: 'destructive', onPress: () => void discard() },
      ],
    );
  }

  async function discard() {
    if (!item) return;
    setBusy(true);
    setError('');
    try {
      // Beim Verwerfen werden absichtlich keine editierbaren Felder gesendet:
      // ungültige Mengen/Timer dürfen diese unabhängige Aktion nie blockieren.
      const result = await api<{ ok: boolean; error?: string }>('/api/pending', {
        method: 'POST',
        body: JSON.stringify({ url: item.url, action: 'skip' }),
      });
      if (!result.ok) throw new Error(result.error || 'Import konnte nicht verworfen werden');
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Verwerfen fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  const externalSource = normalizedExternalUrl(item?.url);
  const hasIngredients = ingredients.some(value => value.name.trim());
  const filename = item?.ai_suggestion?.filename?.trim() || '';
  const extension = filename.split('.').pop()?.toLocaleLowerCase('de-DE') || '';
  const hasLocalFile = ['manual-upload', 'mail-attachment'].includes(
    item?.ai_suggestion?.source || '',
  ) && Boolean(filename);
  const isImage = ['jpg', 'jpeg', 'png'].includes(extension);
  const localFilePath = item
    ? `/api/pending/file?url=${encodeURIComponent(item.url)}`
    : '';
  const compactForm = width < 390 || fontScale > 1.15;

  async function openSource() {
    try {
      await openExternalUrl(externalSource);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Quelle konnte nicht geöffnet werden');
    }
  }

  async function openLocalFile() {
    if (!hasLocalFile || !localFilePath) return;
    const downloadEpoch = currentApiSessionEpoch();
    let localUri = '';
    setBusy(true);
    setError('');
    try {
      const mimeType = extension === 'pdf'
        ? 'application/pdf'
        : extension === 'png' ? 'image/png' : 'image/jpeg';
      localUri = await downloadFileToCache(localFilePath, filename, mimeType);
      assertApiSessionEpochCurrent(downloadEpoch);
      if (!await Sharing.isAvailableAsync()) {
        throw new Error('Die Dateivorschau ist auf diesem Gerät nicht verfügbar.');
      }
      assertApiSessionEpochCurrent(downloadEpoch);
      await Sharing.shareAsync(localUri, {
        mimeType,
        UTI: extension === 'pdf' ? 'com.adobe.pdf' : 'public.image',
        dialogTitle: filename,
      });
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) return;
      setError(reason instanceof Error ? reason.message : 'Datei konnte nicht geöffnet werden');
    } finally {
      if (localUri) await deleteCachedFile(localUri).catch(() => undefined);
      setBusy(false);
    }
  }

  return (
    <Modal visible={item !== null} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe}>
        <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.header}>
            <Pressable accessibilityRole="button" accessibilityLabel="Importprüfung schließen" onPress={onClose} hitSlop={10}><Text style={styles.cancel}>Abbrechen</Text></Pressable>
            <Text style={styles.title}>Import prüfen</Text>
            <View style={{ width: 78 }} />
          </View>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <View style={sharedStyles.card}>
              <Text style={sharedStyles.sectionTitle}>Zuordnung</Text>
              <TextInput placeholder="Rezeptname" placeholderTextColor={colors.muted} value={name} onChangeText={setName} style={sharedStyles.input} />
              <View style={[styles.twoColumns, compactForm && styles.singleColumn]}>
                <TextInput placeholder="Typ" placeholderTextColor={colors.muted} value={recipeType} onChangeText={setRecipeType} style={[sharedStyles.input, styles.flex]} />
                <TextInput placeholder="Kategorie" placeholderTextColor={colors.muted} value={category} onChangeText={setCategory} style={[sharedStyles.input, styles.flex]} />
              </View>
              <TextInput placeholder="Portionen" placeholderTextColor={colors.muted} keyboardType="number-pad" value={servings} onChangeText={setServings} style={sharedStyles.input} />
              <TextInput multiline placeholder="Erkannter Text" placeholderTextColor={colors.muted} value={description} onChangeText={setDescription} style={[sharedStyles.input, styles.description]} />
              <PrimaryButton
                label={aiBusy ? 'KI prüft erneut …' : 'Nochmals mit KI prüfen'}
                onPress={() => void reanalyze()}
                disabled={busy}
              />
              <Text style={styles.aiHelp}>Liest Link, Bild oder PDF erneut aus und ersetzt den Vorschlag erst nach erfolgreicher Analyse.</Text>
            </View>

            <View style={styles.section}>
              <Text style={sharedStyles.sectionTitle}>Zutaten</Text>
              {ingredients.map((ingredient, index) => (
                <View key={ingredient.clientKey} style={styles.rowCard}>
                  <TextInput
                    placeholder={`Zutat ${index + 1}`}
                    placeholderTextColor={colors.muted}
                    value={ingredient.name}
                    onChangeText={value => setIngredients(rows => rows.map(row => row.clientKey === ingredient.clientKey ? { ...row, name: value } : row))}
                    style={sharedStyles.input}
                  />
                  <View style={[styles.twoColumns, compactForm && styles.singleColumn]}>
                    <TextInput
                      placeholder="Menge"
                      placeholderTextColor={colors.muted}
                      keyboardType="decimal-pad"
                      value={ingredient.amount == null ? '' : String(ingredient.amount)}
                      onChangeText={value => setIngredients(rows => rows.map(row => row.clientKey === ingredient.clientKey ? { ...row, amount: value } : row))}
                      style={[sharedStyles.input, styles.flex]}
                    />
                    <UnitPicker
                      value={ingredient.unit}
                      onChange={value => setIngredients(rows => rows.map(row => row.clientKey === ingredient.clientKey ? { ...row, unit: value } : row))}
                      accessibilityLabel={`Mengeneinheit für ${ingredient.name || `Zutat ${index + 1}`}`}
                      style={styles.flex}
                    />
                  </View>
                  <Pressable accessibilityRole="button" accessibilityLabel={`${ingredient.name || `Zutat ${index + 1}`} entfernen`} onPress={() => setIngredients(rows => rows.filter(row => row.clientKey !== ingredient.clientKey))}><Text style={styles.remove}>Entfernen</Text></Pressable>
                </View>
              ))}
              <PrimaryButton label="+ Zutat" onPress={() => setIngredients(rows => [...rows, createIngredientRow()])} />
            </View>

            <View style={styles.section}>
              <Text style={sharedStyles.sectionTitle}>Zubereitung</Text>
              {steps.map((step, index) => (
                <View key={step.clientKey} style={styles.rowCard}>
                  <TextInput
                    multiline
                    placeholder={`Schritt ${index + 1}`}
                    placeholderTextColor={colors.muted}
                    value={step.instruction}
                    onChangeText={value => setSteps(rows => rows.map(row => row.clientKey === step.clientKey ? { ...row, instruction: value } : row))}
                    style={[sharedStyles.input, styles.stepInput]}
                  />
                  <TextInput
                    placeholder="Timer in Sekunden"
                    placeholderTextColor={colors.muted}
                    keyboardType="number-pad"
                    value={step.timer_seconds == null ? '' : String(step.timer_seconds)}
                    onChangeText={value => setSteps(rows => rows.map(row => row.clientKey === step.clientKey ? { ...row, timer_seconds: value } : row))}
                    style={sharedStyles.input}
                  />
                  <Pressable accessibilityRole="button" accessibilityLabel={`Schritt ${index + 1} entfernen`} onPress={() => setSteps(rows => rows.filter(row => row.clientKey !== step.clientKey))}><Text style={styles.remove}>Entfernen</Text></Pressable>
                </View>
              ))}
              <PrimaryButton label="+ Schritt" onPress={() => setSteps(rows => [...rows, createStepRow()])} />
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

            {hasLocalFile && (
              <View style={styles.filePreview}>
                <Text style={styles.fileTitle}>Importdatei · {filename}</Text>
                {isImage && !previewUnavailable && (
                  <Image
                    accessibilityLabel={`Vorschau von ${filename}`}
                    source={{ uri: absoluteApiUrl(localFilePath), headers: apiAuthHeaders() }}
                    contentFit="contain"
                    onError={() => setPreviewUnavailable(true)}
                    style={styles.previewImage}
                  />
                )}
                {previewUnavailable && (
                  <Text style={styles.previewHint}>Keine direkte Vorschau verfügbar. Die Datei kann trotzdem geöffnet werden.</Text>
                )}
                <PrimaryButton label={busy ? 'Datei wird geöffnet …' : 'Importdatei öffnen'} onPress={() => void openLocalFile()} disabled={busy} />
              </View>
            )}
            {!!externalSource && (
              <Pressable accessibilityRole="link" onPress={() => void openSource()}>
                <Text style={styles.source} numberOfLines={2}>Original bei {item?.ai_suggestion?.platform || 'der Plattform'} öffnen ↗</Text>
              </Pressable>
            )}
            {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
            <PrimaryButton label={busy ? 'Speichert …' : 'Rezept speichern'} onPress={() => void save()} disabled={busy || !name.trim()} />
            <PrimaryButton label="Import verwerfen" onPress={requestDiscard} disabled={busy} destructive />
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
  singleColumn: { flexDirection: 'column' },
  flex: { flex: 1 },
  description: { minHeight: 120, paddingTop: 12, textAlignVertical: 'top' },
  aiHelp: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  rowCard: { gap: 8, padding: 10, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  stepInput: { minHeight: 92, paddingTop: 12, textAlignVertical: 'top' },
  remove: { color: colors.danger, minHeight: 30, paddingTop: 5, fontWeight: '700' },
  verifyRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  verifyRowActive: { borderColor: colors.success, backgroundColor: '#EAF6EE' },
  checkbox: { width: 28, height: 28, paddingTop: 3, borderWidth: 2, borderColor: colors.success, borderRadius: 8, color: colors.success, fontWeight: '900', textAlign: 'center' },
  verifyTitle: { color: colors.text, fontWeight: '800' },
  verifyHelp: { color: colors.muted, fontSize: 13, marginTop: 2 },
  disabled: { opacity: 0.45 },
  filePreview: { gap: 10, padding: 12, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  fileTitle: { color: colors.text, fontWeight: '800' },
  previewImage: { width: '100%', minHeight: 220, borderRadius: radii.sm, backgroundColor: colors.cream },
  previewHint: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  source: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  error: { color: colors.danger, lineHeight: 20 },
});
