import { Stack, useRootNavigationState, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { ShareIntentProvider } from 'expo-share-intent';
import React, { PropsWithChildren, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, StyleSheet, Text, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { colors } from '@/constants/design';
import { SharedLinkReceiver } from '@/components/shared-link-receiver';
import { AuthProvider, useAuth } from '@/lib/auth-context';
import { ActiveTimerBar, TimerProvider, useCookingTimers } from '@/lib/timer-context';

function AuthGate({ children }: PropsWithChildren) {
  const { ready, token } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const navigationState = useRootNavigationState();

  useEffect(() => {
    if (!ready || !navigationState?.key) return;
    const isLogin = segments[0] === 'login';
    if (!token && !isLogin) router.replace('/login');
    if (token && isLogin) router.replace('/(tabs)');
  }, [navigationState?.key, ready, router, segments, token]);

  if (!ready) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" color={colors.text} />
      </View>
    );
  }
  return children;
}

function PrivacyShield({ children }: PropsWithChildren) {
  const [hidden, setHidden] = useState(AppState.currentState !== 'active');
  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => setHidden(state !== 'active'));
    return () => subscription.remove();
  }, []);
  return (
    <View style={styles.root}>
      {children}
      {hidden && (
        <View accessibilityElementsHidden style={styles.privacyShield}>
          <Text style={styles.privacyTitle}>Rezepte</Text>
          <Text style={styles.privacyText}>Private Inhalte sind geschützt.</Text>
        </View>
      )}
    </View>
  );
}

function AuthenticatedTimerBar() {
  const { ready, token } = useAuth();
  const { clearAll } = useCookingTimers();
  const previousToken = useRef<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    if (previousToken.current && !token) clearAll();
    previousToken.current = token;
  }, [clearAll, ready, token]);
  return token ? <ActiveTimerBar /> : null;
}

export default function RootLayout() {
  return (
    <ShareIntentProvider options={{ resetOnBackground: true }}>
      <SafeAreaProvider>
        <AuthProvider>
          <TimerProvider>
            <SharedLinkReceiver />
            <PrivacyShield><AuthGate>
              <StatusBar style="dark" />
              <Stack
                screenOptions={{
                  headerTintColor: colors.text,
                  headerStyle: { backgroundColor: colors.cream },
                  headerShadowVisible: false,
                  contentStyle: { backgroundColor: colors.cream },
                  headerBackButtonDisplayMode: 'minimal',
                }}>
                <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
                <Stack.Screen name="login" options={{ headerShown: false, animation: 'fade' }} />
                <Stack.Screen name="recipe/[id]" options={{ title: 'Rezept', presentation: 'card' }} />
                <Stack.Screen name="cook/[id]" options={{ title: 'Kochmodus', presentation: 'card' }} />
              </Stack>
              <AuthenticatedTimerBar />
            </AuthGate></PrivacyShield>
          </TimerProvider>
        </AuthProvider>
      </SafeAreaProvider>
    </ShareIntentProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.butter },
  privacyShield: { ...StyleSheet.absoluteFillObject, zIndex: 9999, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: colors.butter },
  privacyTitle: { color: colors.text, fontSize: 34, fontWeight: '900' },
  privacyText: { color: colors.muted, fontSize: 15 },
});
