import { SymbolView } from 'expo-symbols';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radii } from '@/constants/design';
import { MAX_COOK_SERVINGS, MIN_COOK_SERVINGS, portionLabel } from '@/lib/servings';

export function ServingSelector({
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
  const multiplier = (value / original)
    .toFixed(2)
    .replace(/0+$/, '')
    .replace(/[.,]$/, '')
    .replace('.', ',');

  return (
    <View style={styles.card}>
      <View style={styles.copy}>
        <Text style={styles.title}>Kochen für</Text>
        <Text style={styles.hint}>
          {value === original
            ? `Originalrezept · ${portionLabel(original)}`
            : `Original ${original} · Mengen × ${multiplier}`}
        </Text>
      </View>
      <View style={styles.stepper}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Eine Portion weniger"
          disabled={decreaseDisabled}
          onPress={() => onChange(value - 1)}
          style={({ pressed }) => [styles.button, pressed && styles.pressed, decreaseDisabled && styles.disabled]}>
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
          style={styles.value}>
          <Text style={styles.number}>{value}</Text>
          <Text style={styles.unit}>{value === 1 ? 'Portion' : 'Portionen'}</Text>
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Eine Portion mehr"
          disabled={increaseDisabled}
          onPress={() => onChange(value + 1)}
          style={({ pressed }) => [styles.button, pressed && styles.pressed, increaseDisabled && styles.disabled]}>
          <SymbolView name="plus" size={18} weight="bold" tintColor={colors.text} />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
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
  copy: { flex: 1, minWidth: 150, gap: 3 },
  title: { color: colors.text, fontSize: 16, fontWeight: '800' },
  hint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  stepper: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, overflow: 'hidden' },
  button: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.cream },
  value: { minWidth: 76, height: 48, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, borderLeftWidth: StyleSheet.hairlineWidth, borderRightWidth: StyleSheet.hairlineWidth, borderColor: colors.border, backgroundColor: colors.white },
  number: { color: colors.text, fontSize: 19, lineHeight: 21, fontWeight: '900', fontVariant: ['tabular-nums'] },
  unit: { color: colors.muted, fontSize: 10, lineHeight: 13 },
  pressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
  disabled: { opacity: 0.45 },
});
