import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, PropsWithChildren, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, radii, space } from '@/constants/design';

const STORAGE_KEY = 'rezepte.timers.v1';

type CookingTimer = {
  id: string;
  label: string;
  duration: number;
  remaining: number;
  deadline: number | null;
  running: boolean;
  finished: boolean;
};

type TimerContextValue = {
  timers: Record<string, CookingTimer>;
  toggle: (id: string, label: string, duration: number) => void;
  reset: (id: string, label: string, duration: number) => void;
  remove: (id: string) => void;
  clearAll: () => void;
};

const TimerContext = createContext<TimerContextValue | null>(null);

function reconciled(timer: CookingTimer, now = Date.now()): CookingTimer {
  if (!timer.running || timer.deadline == null) return timer;
  const remaining = Math.max(0, Math.ceil((timer.deadline - now) / 1000));
  if (remaining === 0) {
    return { ...timer, remaining: 0, deadline: null, running: false, finished: true };
  }
  return remaining === timer.remaining ? timer : { ...timer, remaining };
}

export function TimerProvider({ children }: PropsWithChildren) {
  const [timers, setTimers] = useState<Record<string, CookingTimer>>({});
  const [hydrated, setHydrated] = useState(false);
  const lastPersisted = useRef('');
  const hasRunningTimer = Object.values(timers).some(timer => timer.running);

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY)
      .then(raw => {
        if (!raw) return;
        const parsed = JSON.parse(raw) as Record<string, CookingTimer>;
        setTimers(Object.fromEntries(Object.entries(parsed).map(([id, timer]) => [id, reconciled(timer)])));
      })
      .catch(() => AsyncStorage.removeItem(STORAGE_KEY))
      .finally(() => setHydrated(true));
  }, []);

  useEffect(() => {
    if (!hasRunningTimer) return;
    const interval = setInterval(() => {
      setTimers(current => {
        let changed = false;
        const next = Object.fromEntries(Object.entries(current).map(([id, timer]) => {
          const value = reconciled(timer);
          if (value !== timer) changed = true;
          return [id, value];
        }));
        return changed ? next : current;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [hasRunningTimer]);

  useEffect(() => {
    if (!hydrated) return;
    // Bei laufenden Timern ist die Deadline die Quelle der Wahrheit. Das
    // sekündlich wechselnde UI-Feld wird beim Persistieren normalisiert, damit
    // AsyncStorage nicht jede Sekunde beschrieben wird.
    const persistent = Object.fromEntries(Object.entries(timers).map(([id, timer]) => [
      id,
      timer.running ? { ...timer, remaining: timer.duration } : timer,
    ]));
    const serialized = JSON.stringify(persistent);
    if (serialized === lastPersisted.current) return;
    lastPersisted.current = serialized;
    void AsyncStorage.setItem(STORAGE_KEY, serialized);
  }, [hydrated, timers]);

  const value = useMemo<TimerContextValue>(() => ({
    timers,
    toggle(id, label, duration) {
      setTimers(current => {
        const existing = reconciled(current[id] || {
          id,
          label,
          duration,
          remaining: duration,
          deadline: null,
          running: false,
          finished: false,
        });
        if (existing.running) {
          return { ...current, [id]: { ...existing, deadline: null, running: false } };
        }
        const remaining = existing.remaining <= 0 ? duration : existing.remaining;
        return {
          ...current,
          [id]: {
            ...existing,
            label,
            duration,
            remaining,
            deadline: Date.now() + remaining * 1000,
            running: true,
            finished: false,
          },
        };
      });
    },
    reset(id, label, duration) {
      setTimers(current => ({
        ...current,
        [id]: { id, label, duration, remaining: duration, deadline: null, running: false, finished: false },
      }));
    },
    remove(id) {
      setTimers(current => {
        const { [id]: _removed, ...rest } = current;
        return rest;
      });
    },
    clearAll() {
      setTimers({});
    },
  }), [timers]);

  return <TimerContext.Provider value={value}>{children}</TimerContext.Provider>;
}

export function useCookingTimers() {
  const value = useContext(TimerContext);
  if (!value) throw new Error('useCookingTimers muss innerhalb von TimerProvider verwendet werden');
  return value;
}

function format(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${minutes}:${String(rest).padStart(2, '0')}`;
}

export function ActiveTimerBar() {
  const { timers, toggle, remove } = useCookingTimers();
  const insets = useSafeAreaInsets();
  const visible = Object.values(timers)
    .filter(timer => timer.running || timer.finished || timer.remaining !== timer.duration)
    .sort((a, b) => (a.deadline || Number.MAX_SAFE_INTEGER) - (b.deadline || Number.MAX_SAFE_INTEGER))[0];
  if (!visible) return null;
  return (
    <View accessibilityRole="timer" style={[styles.bar, { bottom: insets.bottom + 82 }, visible.finished && styles.finished]}>
      <View style={styles.textWrap}>
        <Text numberOfLines={1} style={styles.label}>{visible.label}</Text>
        <Text style={styles.time}>{visible.finished ? 'Fertig' : format(visible.remaining)}</Text>
      </View>
      {!visible.finished && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={visible.running ? 'Timer pausieren' : 'Timer fortsetzen'}
          onPress={() => toggle(visible.id, visible.label, visible.duration)}
          style={styles.action}>
          <Text style={styles.actionText}>{visible.running ? 'Pause' : 'Weiter'}</Text>
        </Pressable>
      )}
      <Pressable accessibilityRole="button" accessibilityLabel="Timer schließen" onPress={() => remove(visible.id)} style={styles.close}>
        <Text style={styles.closeText}>×</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    position: 'absolute',
    zIndex: 500,
    left: space.md,
    right: space.md,
    minHeight: 58,
    paddingLeft: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: colors.butterPressed,
    borderRadius: radii.md,
    backgroundColor: colors.butter,
    shadowColor: colors.text,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.18,
    shadowRadius: 10,
  },
  finished: { borderColor: colors.success, backgroundColor: '#DDF1E4' },
  textWrap: { flex: 1, gap: 2 },
  label: { color: colors.text, fontSize: 13, fontWeight: '700' },
  time: { color: colors.text, fontSize: 19, fontWeight: '900', fontVariant: ['tabular-nums'] },
  action: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 8 },
  actionText: { color: colors.text, fontWeight: '900' },
  close: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  closeText: { color: colors.text, fontSize: 26 },
});
