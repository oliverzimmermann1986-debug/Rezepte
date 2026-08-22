import React from 'react';
import {
  ActionSheetIOS,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  ViewStyle,
} from 'react-native';

import { colors, radii } from '@/constants/design';
import { normalizeUnit, UNIT_OPTIONS } from '@/lib/units';

type Props = {
  value?: string | null;
  onChange: (value: string) => void;
  accessibilityLabel?: string;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function UnitPicker({
  value,
  onChange,
  accessibilityLabel = 'Mengeneinheit',
  disabled = false,
  style,
}: Props) {
  const normalized = normalizeUnit(value);
  const isKnown = UNIT_OPTIONS.some(option => option.value === normalized);
  const choices = isKnown || !normalized
    ? [...UNIT_OPTIONS]
    : [{ value: normalized, label: `Bisherige Einheit: ${normalized}` }, ...UNIT_OPTIONS];

  function openPicker() {
    if (disabled) return;
    ActionSheetIOS.showActionSheetWithOptions(
      {
        title: 'Mengeneinheit auswählen',
        options: ['Abbrechen', ...choices.map(option => option.label)],
        cancelButtonIndex: 0,
      },
      selectedIndex => {
        const choice = selectedIndex > 0 ? choices[selectedIndex - 1] : undefined;
        if (choice) onChange(choice.value);
      },
    );
  }

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint="Öffnet die Liste der Mengeneinheiten"
      accessibilityValue={{ text: normalized || 'Ohne Einheit' }}
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={openPicker}
      style={({ pressed }) => [styles.control, style, pressed && styles.pressed, disabled && styles.disabled]}>
      <Text style={[styles.value, !normalized && styles.placeholder]} numberOfLines={1}>
        {normalized || 'Ohne Einheit'}
      </Text>
      <Text aria-hidden style={styles.chevron}>⌄</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  control: {
    minHeight: 48,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  value: { flex: 1, color: colors.text, fontSize: 16 },
  placeholder: { color: colors.muted },
  chevron: { color: colors.muted, fontSize: 20, paddingBottom: 5 },
  pressed: { opacity: 0.72 },
  disabled: { opacity: 0.45 },
});
