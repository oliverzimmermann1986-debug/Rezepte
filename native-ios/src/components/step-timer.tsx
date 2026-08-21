import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import { colors } from '@/constants/design';

function format(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

export function StepTimer({ seconds }: { seconds: number }) {
  const [remaining, setRemaining] = useState(seconds);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!running) return;
    if (remaining <= 0) {
      setRunning(false);
      return;
    }
    const timer = setInterval(() => setRemaining(value => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [remaining, running]);

  function toggle() {
    if (remaining === 0) setRemaining(seconds);
    setRunning(value => !value);
  }

  return (
    <Pressable accessibilityRole="button" accessibilityLabel="Schritt-Timer" onPress={toggle} style={styles.button}>
      <Text style={styles.text}>{running || remaining !== seconds ? format(remaining) : `Timer ${format(seconds)}`}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: { minHeight: 44, alignSelf: 'flex-start', justifyContent: 'center', paddingHorizontal: 12 },
  text: { color: colors.text, fontWeight: '800' },
});
