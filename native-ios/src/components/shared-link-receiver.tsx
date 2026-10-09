import { useRootNavigationState, useRouter } from 'expo-router';
import { useShareIntentContext } from 'expo-share-intent';
import { useEffect, useRef } from 'react';
import { Alert } from 'react-native';

import { api } from '@/lib/api';
import { invalidateApiCacheByPrefix } from '@/lib/cache';
import { useAuth } from '@/lib/auth-context';
import { socialLinkFromShareIntent } from '@/lib/shared-link';

type ImportResult = {
  ok: boolean;
  status?: string;
  message?: string;
  recipe_id?: number;
};

export function SharedLinkReceiver() {
  const { canImport, ready, token } = useAuth();
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
    if (!canImport) {
      resetShareIntent();
      processing.current = false;
      Alert.alert('Import für Vollbenutzer und Admins', 'Dieses Konto darf keine Rezeptimporte starten.');
      return;
    }
    const source = socialLinkFromShareIntent(shareIntent);
    if (!source) {
      Alert.alert(
        'Kein Rezept-Link gefunden',
        'Bitte eine Rezept-Webseite, einen Pinterest-Pin, ein YouTube-Video oder einen Social-Beitrag teilen.',
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
      body: JSON.stringify({ url: source, type: 'recipe', visibility: 'private' }),
    })
      .then(async result => {
        await invalidateApiCacheByPrefix('recipes:', 'recipe:');
        if (result.ok) {
          router.replace(result.recipe_id
            ? { pathname: '/recipe/[id]', params: { id: String(result.recipe_id) } }
            : '/(tabs)/account');
        }
        Alert.alert(
          result.ok
            ? 'Link übernommen'
            : 'Import fehlgeschlagen',
          result.message
              || (result.status === 'pending'
                ? 'Der private Import wartet unter „Konto“ auf Prüfung.'
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
    canImport,
    isReady,
    navigationState?.key,
    ready,
    resetShareIntent,
    router,
    shareIntent,
    token,
  ]);

  return null;
}
