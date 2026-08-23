import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { SymbolView } from 'expo-symbols';
import React, { useEffect, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { ServingSelector } from '@/components/serving-selector';
import { StepTimer } from '@/components/step-timer';
import { PrimaryButton, Screen, StateView, sharedStyles } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api, createClientRequestId } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import {
  apiCached,
  invalidateApiCache,
  invalidateApiCacheByPrefix,
  putApiCache,
  readApiCache,
} from '@/lib/cache';
import { formatScaledAmount, normalizedServings, portionLabel } from '@/lib/servings';
import { CookingProgress, RecipeDetail } from '@/lib/types';

type ProgressPayload = {
  completed_steps: number[];
  active_step: number;
  servings: number;
};

export default function CookingModeScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const recipeId = Number(id);
  const router = useRouter();
  const { username } = useAuth();
  const completionStorageKey = `cooking-completion-request:${encodeURIComponent(username || 'session')}:${recipeId}`;
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null);
  const [completed, setCompleted] = useState<number[]>([]);
  const [activeStep, setActiveStep] = useState(0);
  const [servings, setServings] = useState<number | null>(null);
  const [showIngredients, setShowIngredients] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState('');
  const [loadWarning, setLoadWarning] = useState('');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [lastPayload, setLastPayload] = useState<ProgressPayload | null>(null);
  const mutationQueue = useRef<Promise<unknown>>(Promise.resolve());
  const mutationLocked = useRef(false);
  const saveGeneration = useRef(0);
  const completionRequestId = useRef(createClientRequestId());

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setLoadWarning('');
    void Promise.allSettled([
      apiCached<RecipeDetail>(`recipe:${recipeId}`, `/api/recipes/${recipeId}`, controller.signal),
      apiCached<CookingProgress>(
        `cooking-progress:${recipeId}`,
        `/api/recipes/${recipeId}/cooking-progress`,
        controller.signal,
      ),
      readApiCache<string>(completionStorageKey),
    ]).then(([recipeResultState, progressState, completionRequestState]) => {
      if (recipeResultState.status === 'rejected') throw recipeResultState.reason;
      const recipeResult = recipeResultState.value;
      if (!recipeResult.steps.length) throw new Error('Dieses Rezept hat noch keine Zubereitungsschritte.');
      const original = normalizedServings(recipeResult.servings);
      if (!original) {
        throw new Error('Bitte ergänze zuerst die Portionszahl im Rezept. Ohne Ausgangsmenge können Zutaten nicht zuverlässig skaliert werden.');
      }
      const progress = progressState.status === 'fulfilled'
        ? progressState.value
        : {
          recipe_id: recipeId,
          username: '',
          completed_steps: [],
          active_step: 0,
          servings: original,
          exists: false,
          step_count: recipeResult.steps.length,
        };
      setRecipe(recipeResult);
      setCompleted(progress.completed_steps);
      setActiveStep(Math.min(progress.active_step, recipeResult.steps.length - 1));
      setServings(normalizedServings(progress.servings) || original);
      setSaveStatus('idle');
      const storedRequestId = completionRequestState.status === 'fulfilled'
        && typeof completionRequestState.value === 'string'
        && completionRequestState.value.length > 0
        && completionRequestState.value.length <= 200
        ? completionRequestState.value
        : null;
      const nextRequestId = progress.exists && storedRequestId
        ? storedRequestId
        : createClientRequestId();
      completionRequestId.current = nextRequestId;
      if (nextRequestId !== storedRequestId) {
        void putApiCache(completionStorageKey, nextRequestId);
      }
      if (progressState.status === 'rejected') {
        setLoadWarning('Kochfortschritt ist gerade nicht erreichbar. Du kannst mit dem lokal gespeicherten Rezept beginnen.');
      }
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : 'Kochmodus konnte nicht geladen werden.');
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [completionStorageKey, recipeId]);

  function rotateCompletionRequestId() {
    const nextRequestId = createClientRequestId();
    completionRequestId.current = nextRequestId;
    void putApiCache(completionStorageKey, nextRequestId);
  }

  function persistProgress(nextCompleted: number[], nextStep: number, nextServings: number) {
    if (mutationLocked.current) return;
    mutationLocked.current = true;
    const payload: ProgressPayload = {
      completed_steps: [...new Set(nextCompleted)].sort((a, b) => a - b),
      active_step: nextStep,
      servings: nextServings,
    };
    setCompleted(payload.completed_steps);
    setActiveStep(payload.active_step);
    setServings(payload.servings);
    setLastPayload(payload);
    setError('');
    setSaveStatus('saving');
    const generation = ++saveGeneration.current;
    setSaving(true);
    const task = mutationQueue.current
      .catch(() => undefined)
      .then(async () => {
        let optimisticCacheWrite: Promise<void> = Promise.resolve();
        if (recipe) {
          optimisticCacheWrite = putApiCache<CookingProgress>(`cooking-progress:${recipeId}`, {
            recipe_id: recipeId,
            username: '',
            completed_steps: payload.completed_steps,
            active_step: payload.active_step,
            servings: payload.servings,
            exists: true,
            step_count: recipe.steps.length,
            updated_at: Date.now() / 1000,
          });
        }
        const result = await api<CookingProgress>(`/api/recipes/${recipeId}/cooking-progress`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        await optimisticCacheWrite;
        await putApiCache(`cooking-progress:${recipeId}`, result);
        return result;
      });
    mutationQueue.current = task;
    void task.then(() => {
      if (generation === saveGeneration.current) setSaveStatus('saved');
    }).catch(reason => {
      if (generation === saveGeneration.current) {
        setError(reason instanceof Error ? reason.message : 'Fortschritt konnte nicht gespeichert werden.');
        setSaveStatus('error');
      }
    }).finally(() => {
      if (generation === saveGeneration.current) setSaving(false);
      mutationLocked.current = false;
    });
  }

  function toggleCurrentStep() {
    if (mutationLocked.current || !recipe || servings == null) return;
    const isDone = completed.includes(activeStep);
    const nextCompleted = isDone
      ? completed.filter(index => index !== activeStep)
      : [...completed, activeStep];
    const nextStep = !isDone && activeStep < recipe.steps.length - 1
      ? activeStep + 1
      : activeStep;
    persistProgress(nextCompleted, nextStep, servings);
  }

  function selectStep(index: number) {
    if (mutationLocked.current || servings == null) return;
    persistProgress(completed, index, servings);
  }

  function requestReset() {
    if (mutationLocked.current) return;
    Alert.alert(
      'Kochfortschritt zurücksetzen?',
      'Abgehakte Schritte werden gelöscht. Die Kochhistorie bleibt erhalten.',
      [
        { text: 'Abbrechen', style: 'cancel' },
        { text: 'Zurücksetzen', style: 'destructive', onPress: () => void resetProgress() },
      ],
    );
  }

  async function resetProgress() {
    if (mutationLocked.current) return;
    mutationLocked.current = true;
    setSaving(true);
    setError('');
    const task = mutationQueue.current
      .catch(() => undefined)
      .then(async () => {
        await api(`/api/recipes/${recipeId}/cooking-progress`, { method: 'DELETE' });
        await invalidateApiCache(`cooking-progress:${recipeId}`);
      });
    mutationQueue.current = task;
    try {
      await task;
      setCompleted([]);
      setActiveStep(0);
      setLastPayload(null);
      rotateCompletionRequestId();
      setSaveStatus('saved');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Fortschritt konnte nicht zurückgesetzt werden.');
      setSaveStatus('error');
    } finally {
      setSaving(false);
      mutationLocked.current = false;
    }
  }

  async function finishCooking() {
    if (
      mutationLocked.current
      || !recipe
      || servings == null
      || completed.length !== recipe.steps.length
    ) return;
    mutationLocked.current = true;
    setFinishing(true);
    setError('');
    const task = mutationQueue.current
      .catch(() => undefined)
      .then(async () => {
        await api(`/api/recipes/${recipeId}/cooking-complete`, {
          method: 'POST',
          headers: { 'Idempotency-Key': completionRequestId.current },
          body: JSON.stringify({ servings }),
        });
        await Promise.all([
          invalidateApiCache(`cooking-progress:${recipeId}`, `recipe:${recipeId}`),
          invalidateApiCacheByPrefix('recipes:'),
        ]);
      });
    mutationQueue.current = task;
    try {
      await task;
      // Erst eine eindeutig bestätigte Antwort beendet diesen logischen
      // Abschluss. Bei Timeout/Netzfehler bleibt die ID für jeden Retry stabil.
      rotateCompletionRequestId();
      Alert.alert(
        'Guten Appetit!',
        `${recipe.name} wurde für ${portionLabel(servings)} als gekocht gespeichert.`,
        [{ text: 'Fertig', onPress: () => router.back() }],
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Kochabschluss konnte nicht gespeichert werden.');
    } finally {
      setFinishing(false);
      mutationLocked.current = false;
    }
  }

  if (loading) return <Screen><StateView title="Kochmodus wird vorbereitet" loading /></Screen>;
  if (!recipe) return <Screen><StateView title="Kochmodus nicht verfügbar" message={error} action="Zurück" onAction={() => router.back()} /></Screen>;
  if (servings == null) return <Screen><StateView title="Portionszahl fehlt" message="Bitte ergänze die Portionszahl im Rezept, bevor du den Kochmodus startest." action="Zurück zum Rezept" onAction={() => router.back()} /></Screen>;

  const originalServings = normalizedServings(recipe.servings) || servings;
  const multiplier = servings / originalServings;
  const currentStep = recipe.steps[activeStep];
  const currentDone = completed.includes(activeStep);
  const allDone = completed.length === recipe.steps.length;
  const progressWidth = `${Math.round((completed.length / recipe.steps.length) * 100)}%` as `${number}%`;

  return (
    <>
      <Stack.Screen options={{ title: recipe.name }} />
      <Screen contentStyle={styles.content}>
        <View style={styles.progressHeader}>
          <View>
            <Text style={styles.progressTitle}>Schritt {activeStep + 1} von {recipe.steps.length}</Text>
            <Text style={styles.progressMeta} accessibilityLiveRegion="polite">
              {completed.length} erledigt
              {saveStatus === 'saving' ? ' · wird gespeichert …' : saveStatus === 'saved' ? ' · gespeichert' : saveStatus === 'error' ? ' · nicht gespeichert' : ''}
            </Text>
          </View>
          <Pressable accessibilityRole="button" disabled={saving || finishing} onPress={requestReset} style={styles.resetButton}>
            <Text style={styles.resetText}>Neu starten</Text>
          </Pressable>
        </View>
        <View
          accessibilityRole="progressbar"
          accessibilityLabel="Kochfortschritt"
          accessibilityValue={{ min: 0, max: recipe.steps.length, now: completed.length }}
          style={styles.progressTrack}>
          <View style={[styles.progressFill, { width: progressWidth }]} />
        </View>

        {!!loadWarning && <Text accessibilityRole="alert" style={styles.warning}>{loadWarning}</Text>}

        <ServingSelector
          value={servings}
          original={originalServings}
          disabled={saving || finishing}
          onChange={value => persistProgress(completed, activeStep, value)}
        />

        <View style={styles.currentCard}>
          <Text style={styles.stepLabel}>Schritt {activeStep + 1}</Text>
          <Text style={styles.currentInstruction}>{currentStep.instruction}</Text>
          {!!currentStep.timer_seconds && (
            <StepTimer
              id={`recipe-${recipe.id}-step-${currentStep.id || activeStep}`}
              label={`${recipe.name} · Schritt ${activeStep + 1}`}
              seconds={currentStep.timer_seconds}
            />
          )}
          <Pressable
            accessibilityRole="checkbox"
            accessibilityState={{ checked: currentDone }}
            disabled={saving || finishing}
            onPress={toggleCurrentStep}
            style={({ pressed }) => [
              styles.doneButton,
              currentDone && styles.doneButtonActive,
              (saving || finishing) && styles.disabled,
              pressed && styles.pressed,
            ]}>
            <SymbolView name={currentDone ? 'checkmark.circle.fill' : 'circle'} size={22} weight="semibold" tintColor={currentDone ? colors.success : colors.text} />
            <Text style={styles.doneText}>{currentDone ? 'Schritt wieder öffnen' : activeStep < recipe.steps.length - 1 ? 'Erledigt und weiter' : 'Letzten Schritt erledigen'}</Text>
          </Pressable>
        </View>

        <View style={styles.navigationRow}>
          <Pressable
            accessibilityRole="button"
            disabled={activeStep === 0 || saving || finishing}
            onPress={() => selectStep(activeStep - 1)}
            style={({ pressed }) => [styles.navigationButton, (activeStep === 0 || saving || finishing) && styles.disabled, pressed && styles.pressed]}>
            <SymbolView name="chevron.left" size={17} weight="bold" tintColor={colors.text} />
            <Text style={styles.navigationText}>Zurück</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={activeStep === recipe.steps.length - 1 || saving || finishing}
            onPress={() => selectStep(activeStep + 1)}
            style={({ pressed }) => [styles.navigationButton, (activeStep === recipe.steps.length - 1 || saving || finishing) && styles.disabled, pressed && styles.pressed]}>
            <Text style={styles.navigationText}>Weiter</Text>
            <SymbolView name="chevron.right" size={17} weight="bold" tintColor={colors.text} />
          </Pressable>
        </View>

        {!!error && (
          <View style={styles.errorBox}>
            <Text accessibilityRole="alert" style={styles.error}>{error}</Text>
            {!!lastPayload && <PrimaryButton label="Fortschritt erneut speichern" onPress={() => persistProgress(lastPayload.completed_steps, lastPayload.active_step, lastPayload.servings)} disabled={saving || finishing} />}
          </View>
        )}

        <View style={styles.sectionHeader}>
          <Text style={sharedStyles.sectionTitle}>Zutaten</Text>
          <Pressable accessibilityRole="button" accessibilityState={{ expanded: showIngredients }} onPress={() => setShowIngredients(value => !value)} style={styles.inlineAction}>
            <Text style={styles.inlineActionText}>{showIngredients ? 'Ausblenden' : 'Anzeigen'}</Text>
          </Pressable>
        </View>
        {showIngredients && (
          <View style={styles.ingredients}>
            {recipe.ingredients.map((ingredient, index) => (
              <View key={ingredient.id || `${ingredient.name}-${index}`} style={styles.ingredientRow}>
                <Text style={styles.amount}>{formatScaledAmount(ingredient.amount, multiplier)}{ingredient.unit ? ` ${ingredient.unit}` : ''}</Text>
                <Text style={styles.ingredientName}>{ingredient.name}</Text>
              </View>
            ))}
          </View>
        )}

        <Text style={sharedStyles.sectionTitle}>Alle Schritte</Text>
        <View style={styles.stepList}>
          {recipe.steps.map((step, index) => {
            const done = completed.includes(index);
            return (
              <Pressable
                key={step.id || index}
                accessibilityRole="button"
                accessibilityState={{ selected: index === activeStep }}
                disabled={saving || finishing}
                onPress={() => selectStep(index)}
                style={({ pressed }) => [
                  styles.stepRow,
                  index === activeStep && styles.stepRowActive,
                  (saving || finishing) && styles.disabled,
                  pressed && styles.pressed,
                ]}>
                <SymbolView name={done ? 'checkmark.circle.fill' : 'circle'} size={22} weight="semibold" tintColor={done ? colors.success : colors.muted} />
                <Text numberOfLines={2} style={[styles.stepRowText, done && styles.stepRowDone]}>{index + 1}. {step.instruction}</Text>
              </Pressable>
            );
          })}
        </View>

        {allDone ? (
          <View style={styles.finishBlock}>
            <Text style={styles.finishTitle}>Alles erledigt</Text>
            <Text style={styles.finishText}>Der Abschluss trägt dieses Kochen in die Rezept-Historie ein und setzt den Fortschritt zurück.</Text>
            <PrimaryButton label={finishing ? 'Wird abgeschlossen …' : 'Kochen abschließen'} onPress={finishCooking} disabled={finishing || saving} />
          </View>
        ) : (
          <Text style={styles.finishHint}>Hake alle Schritte ab, um das Kochen in der Historie zu speichern.</Text>
        )}
      </Screen>
    </>
  );
}

const styles = StyleSheet.create({
  content: { gap: space.md, paddingBottom: 120 },
  progressHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  progressTitle: { color: colors.text, fontSize: 20, fontWeight: '900' },
  progressMeta: { color: colors.muted, fontSize: 13, marginTop: 3 },
  resetButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 },
  resetText: { color: colors.danger, fontWeight: '800' },
  progressTrack: { height: 8, overflow: 'hidden', borderRadius: 4, backgroundColor: colors.border },
  progressFill: { height: 8, borderRadius: 4, backgroundColor: colors.success },
  currentCard: { padding: space.lg, gap: space.md, borderWidth: 1, borderColor: colors.butterPressed, borderRadius: radii.lg, backgroundColor: '#FFF4CE' },
  stepLabel: { color: colors.muted, fontSize: 13, fontWeight: '900' },
  currentInstruction: { color: colors.text, fontSize: 23, lineHeight: 32, fontWeight: '700' },
  doneButton: { minHeight: 52, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 9, borderRadius: radii.md, backgroundColor: colors.butter },
  doneButtonActive: { backgroundColor: '#EAF6EE' },
  doneText: { color: colors.text, fontSize: 16, fontWeight: '900' },
  navigationRow: { flexDirection: 'row', gap: 10 },
  navigationButton: { flex: 1, minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  navigationText: { color: colors.text, fontWeight: '800' },
  errorBox: { gap: 8 },
  error: { color: colors.danger, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.dangerSurface },
  warning: { color: colors.text, lineHeight: 20, padding: 12, borderRadius: radii.sm, backgroundColor: colors.warningSurface },
  sectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  inlineAction: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 },
  inlineActionText: { color: colors.text, fontWeight: '800' },
  ingredients: { gap: 2 },
  ingredientRow: { minHeight: 46, flexDirection: 'row', alignItems: 'center', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  amount: { width: 92, color: colors.muted, fontVariant: ['tabular-nums'] },
  ingredientName: { flex: 1, color: colors.text, fontSize: 16, fontWeight: '600' },
  stepList: { gap: 8 },
  stepRow: { minHeight: 58, padding: 11, flexDirection: 'row', alignItems: 'center', gap: 10, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  stepRowActive: { borderColor: colors.butterPressed, backgroundColor: '#FFF9E8' },
  stepRowText: { flex: 1, color: colors.text, fontSize: 15, lineHeight: 21 },
  stepRowDone: { color: colors.muted, textDecorationLine: 'line-through' },
  finishBlock: { gap: 8, padding: space.md, borderWidth: 1, borderColor: colors.success, borderRadius: radii.md, backgroundColor: '#EAF6EE' },
  finishTitle: { color: colors.text, fontSize: 19, fontWeight: '900' },
  finishText: { color: colors.muted, lineHeight: 20 },
  finishHint: { color: colors.muted, textAlign: 'center', lineHeight: 20 },
  pressed: { opacity: 0.72, transform: [{ scale: 0.98 }] },
  disabled: { opacity: 0.4 },
});
