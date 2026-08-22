import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { colors } from '@/constants/design';
import { useCookingTimers } from '@/lib/timer-context';

function format(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

export function StepTimer({ id, label, seconds }: { id: string; label: string; seconds: number }) {
  const duration = Math.max(1, Math.round(seconds));
  const { timers, toggle } = useCookingTimers();
  const timer = timers[id];
  const remaining = timer?.remaining ?? duration;
  const running = timer?.running ?? false;

  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label}: Timer`} onPress={() => toggle(id, label, duration)} style={styles.button}>
      <Text style={styles.text}>{timer?.finished ? 'Fertig' : running || remaining !== duration ? format(remaining) : `Timer ${format(duration)}`}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: { minHeight: 44, alignSelf: 'flex-start', justifyContent: 'center', paddingHorizontal: 12 },
  text: { color: colors.text, fontWeight: '800' },
});
