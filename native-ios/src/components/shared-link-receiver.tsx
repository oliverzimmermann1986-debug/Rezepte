import { useRootNavigationState, useRouter } from 'expo-router';
import { useShareIntentContext } from 'expo-share-intent';
import { useEffect, useRef } from 'react';
import { Alert } from 'react-native';

import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { socialLinkFromSharedContent } from '@/lib/shared-link';

type ImportResult = {
  ok: boolean;
  status?: string;
  message?: string;
};

export function SharedLinkReceiver() {
  const { ready, token } = useAuth();
  const { error, hasShareIntent, isReady, resetShareIntent, shareIntent } = useShareIntentContext();
  const navigationState = useRootNavigationState();
  const router = useRouter();
  const processing = useRef(false);

  useEffect(() => {
    if (!error || processing.current) return;
    processing.current = true;
    Alert.alert('Teilen nicht möglich', error, [
      {
        text: 'OK',
        onPress: () => {
          resetShareIntent();
          processing.current = false;
        },
      },
    ]);
  }, [error, resetShareIntent]);

  useEffect(() => {
    if (
      processing.current
      || !ready
      || !token
      || !isReady
      || !hasShareIntent
      || !navigationState?.key
    ) return;

    processing.current = true;
    const source = socialLinkFromSharedContent(shareIntent.webUrl, shareIntent.text);
    if (!source) {
      Alert.alert(
        'Kein Rezept-Link gefunden',
        'Bitte einen TikTok- oder Instagram-Beitrag teilen.',
        [{
          text: 'OK',
          onPress: () => {
            resetShareIntent();
            processing.current = false;
          },
        }],
      );
      return;
    }

    void api<ImportResult>('/api/pending/import-url', {
      method: 'POST',
      body: JSON.stringify({ url: source, type: 'recipe' }),
    })
      .then(result => {
        router.replace('/(tabs)/admin');
        Alert.alert(
          result.ok ? 'Link übernommen' : 'Import fehlgeschlagen',
          result.message
            || (result.status === 'pending'
              ? 'Der Beitrag wartet unter „Manuelle Prüfung“.'
              : 'Der Beitrag wurde verarbeitet.'),
        );
      })
      .catch(reason => {
        Alert.alert(
          'Link nicht übernommen',
          reason instanceof Error ? reason.message : 'Der Import konnte nicht gestartet werden.',
        );
      })
      .finally(() => {
        resetShareIntent();
        processing.current = false;
      });
  }, [
    hasShareIntent,
    isReady,
    navigationState?.key,
    ready,
    resetShareIntent,
    router,
    shareIntent.text,
    shareIntent.webUrl,
    token,
  ]);

  return null;
}
