import React, { PropsWithChildren } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, radii, space } from '@/constants/design';

export function Screen({
  children,
  scroll = true,
  contentStyle,
  topSafe = false,
}: PropsWithChildren<{ scroll?: boolean; contentStyle?: ViewStyle; topSafe?: boolean }>) {
  if (!scroll) return <SafeAreaView style={[styles.screen, contentStyle]}>{children}</SafeAreaView>;
  return (
    <SafeAreaView style={styles.screen} edges={topSafe ? ['top', 'left', 'right'] : ['left', 'right']}>
      <ScrollView
        contentContainerStyle={[styles.content, contentStyle]}
        keyboardShouldPersistTaps="handled"
        contentInsetAdjustmentBehavior="automatic">
        {children}
      </ScrollView>
    </SafeAreaView>
  );
}

export function PrimaryButton({
  label,
  onPress,
  disabled,
  destructive,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        destructive && styles.destructiveButton,
        pressed && styles.pressed,
        disabled && styles.disabled,
      ]}>
      <Text style={[styles.buttonText, destructive && styles.destructiveText]}>{label}</Text>
    </Pressable>
  );
}

export function StateView({
  title,
  message,
  loading,
  action,
  onAction,
}: {
  title: string;
  message?: string;
  loading?: boolean;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <View style={styles.state}>
      {loading && <ActivityIndicator color={colors.text} />}
      <Text style={styles.stateTitle}>{title}</Text>
      {!!message && <Text style={styles.stateMessage}>{message}</Text>}
      {action && onAction && <PrimaryButton label={action} onPress={onAction} />}
    </View>
  );
}

export function ManualCareBanner({
  reasons,
  onOpenSource,
}: {
  reasons: string[];
  onOpenSource?: () => void;
}) {
  return (
    <View accessibilityRole="alert" style={styles.warning}>
      <View style={styles.warningText}>
        <Text style={styles.warningTitle}>Manuelle Pflege erforderlich</Text>
        <Text style={styles.warningMessage}>{reasons.join(' · ') || 'Rezept ist unvollständig'}</Text>
      </View>
      {onOpenSource && (
        <Pressable accessibilityRole="link" onPress={onOpenSource} hitSlop={8}>
          <Text style={styles.warningLink}>Quelle öffnen ↗</Text>
        </Pressable>
      )}
    </View>
  );
}

export const sharedStyles = StyleSheet.create({
  sectionTitle: { color: colors.text, fontSize: 21, fontWeight: '800' },
  card: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
    padding: space.md,
    gap: space.sm,
  },
  input: {
    minHeight: 50,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.white,
    paddingHorizontal: 14,
    color: colors.text,
    fontSize: 16,
  },
});

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.cream },
  content: { padding: space.md, paddingBottom: 48, gap: space.md },
  button: {
    minHeight: 50,
    paddingHorizontal: 18,
    borderRadius: radii.md,
    backgroundColor: colors.butter,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonText: { color: colors.text, fontSize: 16, fontWeight: '800' },
  destructiveButton: { backgroundColor: colors.dangerSurface },
  destructiveText: { color: colors.danger },
  pressed: { opacity: 0.78, transform: [{ scale: 0.98 }] },
  disabled: { opacity: 0.45 },
  state: { flex: 1, minHeight: 300, alignItems: 'center', justifyContent: 'center', gap: space.sm },
  stateTitle: { color: colors.text, fontSize: 20, fontWeight: '800', textAlign: 'center' },
  stateMessage: { color: colors.muted, fontSize: 15, lineHeight: 21, textAlign: 'center' },
  warning: {
    padding: 14,
    borderWidth: 1,
    borderColor: '#D69A48',
    backgroundColor: colors.warningSurface,
    borderRadius: radii.md,
    gap: space.sm,
  },
  warningText: { gap: 2 },
  warningTitle: { color: colors.text, fontSize: 16, fontWeight: '800' },
  warningMessage: { color: colors.warning, lineHeight: 20 },
  warningLink: { color: colors.text, fontWeight: '800', minHeight: 32, paddingTop: 6 },
});
