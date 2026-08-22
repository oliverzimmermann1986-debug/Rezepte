import { Image } from 'expo-image';
import { Stack, useLocalSearchParams } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { SymbolView } from 'expo-symbols';
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Pressable, Share, StyleSheet, Text, View } from 'react-native';

import { RecipeEditor } from '@/components/recipe-editor';
import { RecipeMetadataEditor } from '@/components/recipe-metadata-editor';
import { RecipeShareLinks } from '@/components/recipe-share-links';
import { StepTimer } from '@/components/step-timer';
import { ManualCareBanner, PrimaryButton, Screen, StateView, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { absoluteApiUrl, api, apiAuthHeaders, deleteCachedFile, downloadFileToCache, uploadFile } from '@/lib/api';
import { apiCached } from '@/lib/cache';
import { externalSourceLabel, openExternalUrl } from '@/lib/external-links';
import { pickEditedJpeg } from '@/lib/image-picker';
import { RecipeDetail } from '@/lib/types';

type Tab = 'info' | 'ingredients' | 'steps';
type SymbolName = React.ComponentProps<typeof SymbolView>['name'];

const MIN_COOK_SERVINGS = 1;
const MAX_COOK_SERVINGS = 50;

function normalizedServings(value?: number | null) {
  if (!value || !Number.isFinite(value) || value < MIN_COOK_SERVINGS) return null;
  return Math.min(MAX_COOK_SERVINGS, Math.round(value));
}

function portionLabel(value: number) {
  return `${value} ${value === 1 ? 'Portion' : 'Portionen'}`;
}

function formatScaledAmount(value: number | null | undefined, multiplier: number) {
  if (value === null || value === undefined) return '–';
  const rounded = Math.round(value * multiplier * 100) / 100;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace('.', ',');
}

function CompactAction({
  label,
  symbol,
  onPress,
  disabled,
}: {
  label: string;
  symbol: SymbolName;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.compactAction, pressed && styles.actionPressed, disabled && styles.disabled]}>
      <SymbolView name={symbol} size={18} weight="semibold" tintColor={colors.text} />
      <Text style={styles.compactActionText}>{label}</Text>
    </Pressable>
  );
}

function ServingSelector({
  value,
  original,
  disabled,
  onChange,
}: {
  value: number;
  original: number;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  const decreaseDisabled = disabled || value <= MIN_COOK_SERVINGS;
  const increaseDisabled = disabled || value >= MAX_COOK_SERVINGS;

  return (
    <View style={styles.servingCard}>
      <View style={styles.servingCopy}>
        <Text style={styles.servingTitle}>Kochen für</Text>
        <Text style={styles.servingHint}>
          {value === original ? `Originalrezept · ${portionLabel(original)}` : `Original ${original} · Mengen × ${(value / original).toFixed(2).replace(/0+$/, '').replace(/[.,]$/, '').replace('.', ',')}`}
        </Text>
      </View>
      <View style={styles.servingStepper}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Eine Portion weniger"
          disabled={decreaseDisabled}
          onPress={() => onChange(value - 1)}
          style={({ pressed }) => [styles.servingButton, pressed && styles.actionPressed, decreaseDisabled && styles.disabled]}>
          <SymbolView name="minus" size={18} weight="bold" tintColor={colors.text} />
        </Pressable>
        <View
          accessibilityRole="adjustable"
          accessibilityLabel="Anzahl Portionen"
          accessibilityValue={{ min: MIN_COOK_SERVINGS, max: MAX_COOK_SERVINGS, now: value, text: portionLabel(value) }}
          accessibilityActions={[{ name: 'decrement', label: 'Eine Portion weniger' }, { name: 'increment', label: 'Eine Portion mehr' }]}
          onAccessibilityAction={({ nativeEvent }) => {
            if (nativeEvent.actionName === 'decrement' && !decreaseDisabled) onChange(value - 1);
            if (nativeEvent.actionName === 'increment' && !increaseDisabled) onChange(value + 1);
          }}
          style={styles.servingValue}>
          <Text style={styles.servingNumber}>{value}</Text>
          <Text style={styles.servingUnit}>{value === 1 ? 'Portion' : 'Portionen'}</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Eine Portion mehr"
          disabled={increaseDisabled}
          onPress={() => onChange(value + 1)}
          style={({ pressed }) => [styles.servingButton, pressed && styles.actionPressed, increaseDisabled && styles.disabled]}>
          <SymbolView name="plus" size={18} weight="bold" tintColor={colors.text} />
        </Pressable>
      </View>
    </View>
  );
}

export default function RecipeDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const recipeId = Number(id);
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null);
  const [tab, setTab] = useState<Tab>('steps');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState<'ingredients' | 'steps' | null>(null);
  const [metadataEditor, setMetadataEditor] = useState(false);
  const [shareLinks, setShareLinks] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imageVersion, setImageVersion] = useState(0);
  const [showOriginal, setShowOriginal] = useState(false);
  const [cookServings, setCookServings] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setRecipe(await apiCached<RecipeDetail>(`recipe:${recipeId}`, `/api/recipes/${recipeId}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Rezept konnte nicht geladen werden');
    } finally {
      setLoading(false);
    }
  }, [recipeId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    setCookServings(normalizedServings(recipe?.servings));
  }, [recipe?.id, recipe?.servings]);

  async function toggleFavorite() {
    if (!recipe) return;
    setBusy(true);
    try {
      const result = await api<{ is_favorite: boolean }>(`/api/recipes/${recipe.id}/favorite`, { method: 'POST' });
      setRecipe({ ...recipe, is_favorite: result.is_favorite });
    } catch (reason) {
      Alert.alert('Favorit nicht geändert', reason instanceof Error ? reason.message : 'Bitte erneut versuchen.');
    } finally {
      setBusy(false);
    }
  }

  async function addToCart() {
    if (!recipe) return;
    const originalServings = normalizedServings(recipe.servings);
    const selectedServings = originalServings ? (cookServings || originalServings) : null;
    const multiplier = originalServings && selectedServings ? selectedServings / originalServings : 1;
    setBusy(true);
    try {
      const result = await api<{ added: number; merged: number; skipped: number }>(`/api/cart/cook/${recipe.id}`, {
        method: 'POST',
        // Der Faktor hält die App während eines gestaffelten Rollouts mit dem
        // bisherigen Backend kompatibel; der neue Server prüft beide Werte.
        body: JSON.stringify(selectedServings ? { servings: selectedServings, multiplier } : { multiplier: 1 }),
      });
      const changed = result.added + result.merged;
      const scope = selectedServings ? ` für ${portionLabel(selectedServings)}` : ' in Originalmenge';
      Alert.alert('Zum Einkauf hinzugefügt', `${changed} Artikel${scope} übernommen${result.skipped ? ` · ${result.skipped} ausgeschlossen` : ''}.`);
    } catch (reason) {
      Alert.alert('Nicht möglich', reason instanceof Error ? reason.message : 'Hinzufügen fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  async function openSource() {
    if (!recipe?.url) return;
    try {
      await openExternalUrl(recipe.url);
    } catch (reason) {
      Alert.alert('Quelle nicht geöffnet', reason instanceof Error ? reason.message : 'Bitte erneut versuchen.');
    }
  }

  function shareRecipe() {
    if (!recipe) return;
    Alert.alert(
      'Öffentlichen Link erstellen?',
      'Jeder mit dem Link kann dieses Rezept 7 Tage lang ohne Anmeldung sehen.',
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Freigaben verwalten', onPress: () => setShareLinks(true) },
        { text: 'Link erstellen', onPress: createShareLink },
      ],
    );
  }

  async function createShareLink() {
    if (!recipe) return;
    setBusy(true);
    try {
      const result = await api<{ url: string }>(`/api/recipes/${recipe.id}/share`, {
        method: 'POST',
        body: JSON.stringify({ expires_days: 7 }),
      });
      await Share.share({ title: recipe.name, message: `${recipe.name}\n${result.url}`, url: result.url });
    } catch (reason) {
      Alert.alert('Teilen nicht möglich', reason instanceof Error ? reason.message : 'Share-Link fehlgeschlagen');
    } finally {
      setBusy(false);
    }
  }

  async function changeImage() {
    if (!recipe) return;
    let imageUri = '';
    try {
      const image = await pickEditedJpeg(`rezeptbild-${recipe.id}`);
      if (!image) return;
      imageUri = image.uri;
      setBusy(true);
      await uploadFile(`/api/recipes/${recipe.id}/upload-thumbnail`, image);
      await Image.clearMemoryCache();
      setImageVersion(Date.now());
      Alert.alert('Bild gespeichert', 'Das zugeschnittene Rezeptbild wurde übernommen.');
    } catch (reason) {
      Alert.alert('Bild nicht gespeichert', reason instanceof Error ? reason.message : 'Upload fehlgeschlagen');
    } finally {
      if (imageUri) await deleteCachedFile(imageUri).catch(() => undefined);
      setBusy(false);
    }
  }

  async function openPdf() {
    if (!recipe?.pdf_filename) return;
    setBusy(true);
    let localUri = '';
    try {
      localUri = await downloadFileToCache(
        `/api/recipes/${recipe.id}/pdf`,
        recipe.pdf_filename,
      );
      if (!await Sharing.isAvailableAsync()) throw new Error('PDF-Vorschau ist auf diesem Gerät nicht verfügbar.');
      await Sharing.shareAsync(localUri, {
        mimeType: 'application/pdf',
        UTI: 'com.adobe.pdf',
        dialogTitle: `${recipe.name} – Original-PDF`,
      });
    } catch (reason) {
      Alert.alert('PDF nicht geöffnet', reason instanceof Error ? reason.message : 'Download fehlgeschlagen');
    } finally {
      if (localUri) await deleteCachedFile(localUri).catch(() => undefined);
      setBusy(false);
    }
  }

  async function setRating(value: number) {
    if (!recipe) return;
    const next = recipe.rating === value ? 0 : value;
    setBusy(true);
    try {
      const result = await api<{ rating: number }>(`/api/recipes/${recipe.id}/rating?value=${next}`, { method: 'POST' });
      setRecipe({ ...recipe, rating: result.rating });
    } catch (reason) {
      Alert.alert('Bewertung nicht gespeichert', reason instanceof Error ? reason.message : 'Unbekannter Fehler');
    } finally {
      setBusy(false);
    }
  }

  async function toggleVerified() {
    if (!recipe) return;
    const next = !Boolean(recipe.user_verified);
    setBusy(true);
    try {
      const result = await api<{ verified: boolean; by?: string }>(`/api/recipes/${recipe.id}/verify?verified=${next}`, { method: 'POST' });
      setRecipe({ ...recipe, user_verified: result.verified, verified_by: result.by && result.by !== '?' ? result.by : null });
    } catch (reason) {
      Alert.alert('Prüfstatus nicht gespeichert', reason instanceof Error ? reason.message : 'Unbekannter Fehler');
    } finally {
      setBusy(false);
    }
  }

  if (loading && !recipe) return <Screen><StateView title="Rezept wird geladen" loading /></Screen>;
  if (error && !recipe) {
    return <Screen><StateView title="Rezept nicht verfügbar" message={error} action="Erneut versuchen" onAction={load} /></Screen>;
  }
  if (!recipe) return null;

  const sourcePlatform = externalSourceLabel(recipe.url);
  const originalServings = normalizedServings(recipe.servings);
  const selectedServings = originalServings ? (cookServings || originalServings) : null;
  const cookMultiplier = originalServings && selectedServings ? selectedServings / originalServings : 1;

  return (
    <>
      <Stack.Screen options={{ title: recipe.name }} />
      <Screen contentStyle={styles.content}>
        <View style={styles.heroWrap}>
          <Image
            source={{ uri: absoluteApiUrl(`/api/recipes/${recipe.id}/thumb?w=1000&v=${imageVersion}`), headers: apiAuthHeaders() }}
            style={styles.hero}
            contentFit="cover"
            cachePolicy="memory"
            transition={150}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Rezeptbild ändern"
            disabled={busy}
            onPress={changeImage}
            style={({ pressed }) => [styles.imageEdit, pressed && styles.imageEditPressed, busy && styles.disabled]}>
            <SymbolView name="pencil" size={20} weight="bold" tintColor={colors.text} />
          </Pressable>
        </View>
        <View style={styles.titleRow}>
          <View style={styles.titleText}>
            <Text style={styles.title}>{recipe.name}</Text>
            <Text style={styles.meta}>{[recipe.type, recipe.category].filter(Boolean).join(' · ')}</Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={recipe.is_favorite ? 'Favorit entfernen' : 'Als Favorit markieren'}
            onPress={toggleFavorite}
            disabled={busy}
            style={styles.favorite}>
            <Text style={styles.favoriteText}>{recipe.is_favorite ? '★' : '☆'}</Text>
          </Pressable>
        </View>

        <View style={styles.ratingRow}>
          <Text style={styles.ratingLabel}>Bewertung</Text>
          <View style={styles.stars}>
            {[1, 2, 3, 4, 5].map(value => (
              <Pressable
                key={value}
                accessibilityRole="button"
                accessibilityLabel={`${value} Sterne`}
                accessibilityState={{ selected: recipe.rating === value }}
                onPress={() => setRating(value)}
                disabled={busy}
                hitSlop={5}>
                <Text style={[styles.star, value <= recipe.rating && styles.starActive]}>★</Text>
              </Pressable>
            ))}
          </View>
        </View>

        <View style={styles.actionRow}>
          {!!recipe.url && (
            <CompactAction label={sourcePlatform} symbol="arrow.up.right.square" onPress={openSource} disabled={busy} />
          )}
          {!!recipe.pdf_filename && (
            <CompactAction label="PDF" symbol="doc" onPress={openPdf} disabled={busy} />
          )}
          <CompactAction label="Teilen" symbol="square.and.arrow.up" onPress={shareRecipe} disabled={busy} />
        </View>

        {recipe.needs_manual_care && (
          <ManualCareBanner reasons={recipe.manual_care_reasons} onOpenSource={recipe.url ? openSource : undefined} />
        )}

        {originalServings && selectedServings ? (
          <ServingSelector
            value={selectedServings}
            original={originalServings}
            disabled={busy}
            onChange={setCookServings}
          />
        ) : (
          <View style={styles.servingCard}>
            <View style={styles.servingCopy}>
              <Text style={styles.servingTitle}>Kochen für</Text>
              <Text style={styles.servingHint}>Portionszahl fehlt – zum Skalieren bitte ergänzen.</Text>
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Portionszahl im Rezept ergänzen"
              disabled={busy}
              onPress={() => setMetadataEditor(true)}
              style={({ pressed }) => [styles.servingMissingAction, pressed && styles.actionPressed, busy && styles.disabled]}>
              <Text style={styles.servingMissingText}>Ergänzen</Text>
            </Pressable>
          </View>
        )}

        <View style={styles.tabs}>
          {([
            ['steps', 'Zubereitung'],
            ['ingredients', 'Zutaten'],
            ['info', 'Info'],
          ] as [Tab, string][]).map(([value, label]) => (
            <Pressable
              key={value}
              accessibilityRole="tab"
              accessibilityState={{ selected: tab === value }}
              onPress={() => setTab(value)}
              style={[styles.tab, tab === value && styles.tabActive]}>
              <Text style={[styles.tabText, tab === value && styles.tabTextActive]}>{label}</Text>
            </Pressable>
          ))}
        </View>

        {tab === 'info' && (
          <View style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={sharedStyles.sectionTitle}>Informationen</Text>
              <Pressable accessibilityRole="button" accessibilityLabel="Rezeptinformationen bearbeiten" onPress={() => setMetadataEditor(true)} hitSlop={8}>
                <Text style={styles.edit}>Bearbeiten</Text>
              </Pressable>
            </View>
            {!!recipe.servings && <Text style={styles.infoMeta}>{recipe.servings} Portionen</Text>}
            {!!recipe.description && <Text style={styles.description}>{recipe.description}</Text>}
            <View style={styles.tagRow}>
              {recipe.tags.map(tag => <Text key={tag.id} style={styles.tag}>{tag.name}</Text>)}
            </View>
            <Text style={styles.noVideo}>Videos werden in der App bewusst nicht geladen oder abgespielt.</Text>
            {!!recipe.description_original && (
              <View style={styles.originalBlock}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ expanded: showOriginal }}
                  onPress={() => setShowOriginal(value => !value)}>
                  <Text style={styles.originalButton}>{showOriginal ? 'Originaltext ausblenden' : 'Originaltext anzeigen'}</Text>
                </Pressable>
                {showOriginal && <Text selectable style={styles.originalText}>{recipe.description_original}</Text>}
              </View>
            )}
          </View>
        )}

        {tab === 'ingredients' && (
          <View style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={sharedStyles.sectionTitle}>Zutaten</Text>
              <Pressable accessibilityRole="button" accessibilityLabel="Zutaten bearbeiten" onPress={() => setEditor('ingredients')} hitSlop={8}>
                <Text style={styles.edit}>Bearbeiten</Text>
              </Pressable>
            </View>
            {recipe.ingredients.length ? recipe.ingredients.map((ingredient, index) => (
              <View key={ingredient.id || `${ingredient.name}-${index}`} style={styles.ingredient}>
                <Text style={styles.amount}>
                  {formatScaledAmount(ingredient.amount, cookMultiplier)}{ingredient.unit ? ` ${ingredient.unit}` : ''}
                </Text>
                <Text style={styles.ingredientName}>{ingredient.name}</Text>
              </View>
            )) : <Text style={styles.empty}>Keine Zutaten vorhanden. Bitte manuell ergänzen.</Text>}
            <Pressable
              accessibilityRole="checkbox"
              accessibilityState={{ checked: Boolean(recipe.user_verified) }}
              onPress={toggleVerified}
              disabled={busy || !recipe.ingredients.length}
              style={[styles.verifiedRow, Boolean(recipe.user_verified) && styles.verifiedRowActive, !recipe.ingredients.length && styles.disabled]}>
              <Text style={styles.verifiedCheck}>{recipe.user_verified ? '✓' : ''}</Text>
              <View style={styles.verifiedText}>
                <Text style={styles.verifiedTitle}>Zutaten als geprüft markieren</Text>
                <Text style={styles.verifiedHelp}>
                  {recipe.user_verified ? `Manuell geprüft${recipe.verified_by ? ` von ${recipe.verified_by}` : ''}` : 'Bestätigt, dass die Zutatenliste kontrolliert wurde.'}
                </Text>
              </View>
            </Pressable>
            <PrimaryButton
              label={busy ? 'Wird hinzugefügt …' : selectedServings ? `Für ${portionLabel(selectedServings)} einkaufen` : 'Originalmenge einkaufen'}
              onPress={addToCart}
              disabled={busy || !recipe.ingredients.length}
            />
          </View>
        )}

        {tab === 'steps' && (
          <View style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={sharedStyles.sectionTitle}>Zubereitung</Text>
              <Pressable accessibilityRole="button" accessibilityLabel="Schritte bearbeiten" onPress={() => setEditor('steps')} hitSlop={8}>
                <Text style={styles.edit}>Bearbeiten</Text>
              </Pressable>
            </View>
            {recipe.steps.length ? recipe.steps.map((step, index) => (
              <View key={step.id || index} style={styles.step}>
                <Text style={styles.stepNumber}>{index + 1}</Text>
                <View style={styles.stepBody}>
                  <Text style={styles.stepText}>{step.instruction}</Text>
                  {!!step.timer_seconds && (
                    <StepTimer
                      id={`recipe-${recipe.id}-step-${step.id || index}`}
                      label={`${recipe.name} · Schritt ${index + 1}`}
                      seconds={step.timer_seconds}
                    />
                  )}
                </View>
              </View>
            )) : <Text style={styles.empty}>Keine Schritte vorhanden. Bitte manuell ergänzen.</Text>}
          </View>
        )}
      </Screen>
      <RecipeEditor
        recipeId={recipe.id}
        kind={editor || 'ingredients'}
        ingredients={recipe.ingredients}
        steps={recipe.steps}
        visible={editor !== null}
        onClose={() => setEditor(null)}
        onSaved={async () => {
          setEditor(null);
          await load();
        }}
      />
      <RecipeMetadataEditor
        recipe={recipe}
        visible={metadataEditor}
        onClose={() => setMetadataEditor(false)}
        onSaved={async () => {
          setMetadataEditor(false);
          await load();
        }}
      />
      <RecipeShareLinks recipeId={recipe.id} visible={shareLinks} onClose={() => setShareLinks(false)} />
    </>
  );
}

const styles = StyleSheet.create({
  content: { gap: space.md },
  heroWrap: { position: 'relative' },
  hero: { width: '100%', aspectRatio: 16 / 10, borderRadius: radii.lg, backgroundColor: colors.border },
  imageEdit: { position: 'absolute', right: 12, bottom: 12, width: 48, height: 48, alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: colors.surface, borderRadius: 24, backgroundColor: colors.butter, shadowColor: '#433427', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.2, shadowRadius: 7 },
  imageEditPressed: { transform: [{ scale: 0.94 }], backgroundColor: colors.butterPressed },
  titleRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  titleText: { flex: 1, gap: 4 },
  title: { color: colors.text, fontSize: 30, lineHeight: 34, letterSpacing: -0.7, fontWeight: '900' },
  meta: { color: colors.muted, fontSize: 14 },
  ratingRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: 44 },
  ratingLabel: { color: colors.text, fontSize: 16, fontWeight: '800' },
  stars: { flexDirection: 'row', gap: 5 },
  star: { color: colors.border, fontSize: 28 },
  starActive: { color: colors.butterPressed },
  favorite: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center' },
  favoriteText: { color: colors.text, fontSize: 30 },
  actionRow: { minHeight: 48, flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  compactAction: { minHeight: 44, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, borderWidth: 1, borderColor: colors.border, borderRadius: 22, backgroundColor: colors.surface },
  compactActionText: { color: colors.text, fontSize: 14, fontWeight: '800' },
  actionPressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
  servingCard: {
    minHeight: 76,
    padding: 12,
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  servingCopy: { flex: 1, minWidth: 150, gap: 3 },
  servingTitle: { color: colors.text, fontSize: 16, fontWeight: '800' },
  servingHint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  servingStepper: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, overflow: 'hidden' },
  servingButton: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.cream },
  servingValue: { minWidth: 76, height: 48, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, borderLeftWidth: StyleSheet.hairlineWidth, borderRightWidth: StyleSheet.hairlineWidth, borderColor: colors.border, backgroundColor: colors.white },
  servingNumber: { color: colors.text, fontSize: 19, lineHeight: 21, fontWeight: '900', fontVariant: ['tabular-nums'] },
  servingUnit: { color: colors.muted, fontSize: 10, lineHeight: 13 },
  servingMissingAction: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderRadius: radii.sm, backgroundColor: colors.butter },
  servingMissingText: { color: colors.text, fontSize: 14, fontWeight: '800' },
  tabs: { flexDirection: 'row', padding: 4, borderRadius: radii.md, backgroundColor: '#EEE4D6' },
  tab: { flex: 1, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radii.sm },
  tabActive: { backgroundColor: colors.surface },
  tabText: { color: colors.muted, fontSize: 13, fontWeight: '700' },
  tabTextActive: { color: colors.text },
  section: { gap: 12 },
  sectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  edit: { color: colors.text, minHeight: 36, paddingTop: 8, fontWeight: '800' },
  description: { color: colors.text, fontSize: 17, lineHeight: 25 },
  infoMeta: { color: colors.muted, fontSize: 14, fontWeight: '700' },
  noVideo: { color: colors.muted, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  originalBlock: { marginTop: space.md, gap: 10, paddingTop: space.md, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  originalButton: { color: colors.text, minHeight: 44, paddingTop: 12, textAlign: 'center', fontWeight: '800' },
  originalText: { color: colors.muted, fontSize: 14, lineHeight: 21 },
  tagRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  tag: { color: colors.text, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999, backgroundColor: '#EFE5D8' },
  ingredient: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  amount: { width: 94, color: colors.muted, fontVariant: ['tabular-nums'] },
  ingredientName: { flex: 1, color: colors.text, fontSize: 17, fontWeight: '600' },
  verifiedRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 13, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  verifiedRowActive: { borderColor: colors.success, backgroundColor: '#EAF6EE' },
  verifiedCheck: { width: 28, height: 28, paddingTop: 3, borderWidth: 2, borderColor: colors.success, borderRadius: 8, color: colors.success, fontWeight: '900', textAlign: 'center' },
  verifiedText: { flex: 1, gap: 2 },
  verifiedTitle: { color: colors.text, fontWeight: '800' },
  verifiedHelp: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  disabled: { opacity: 0.45 },
  empty: { color: colors.warning, padding: 14, borderRadius: radii.md, backgroundColor: colors.warningSurface },
  step: { flexDirection: 'row', gap: 12, paddingVertical: 12 },
  stepNumber: {
    width: 36,
    height: 36,
    borderRadius: 18,
    textAlign: 'center',
    paddingTop: 8,
    overflow: 'hidden',
    color: colors.text,
    backgroundColor: colors.butter,
    fontWeight: '900',
  },
  stepBody: { flex: 1, gap: 8 },
  stepText: { color: colors.text, fontSize: 18, lineHeight: 27 },
});
