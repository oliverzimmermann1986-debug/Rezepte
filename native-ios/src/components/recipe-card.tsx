import { Image } from 'expo-image';
import { router } from 'expo-router';
import { SymbolView } from 'expo-symbols';
import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';

import { colors, radii, space } from '@/constants/design';
import { absoluteApiUrl, apiAuthHeaders } from '@/lib/api';
import { RecipeListItem } from '@/lib/types';
import { recipeStatus } from '@/lib/recipe-status';

export function RecipeCard({
  recipe,
  deleting = false,
  onDelete,
}: {
  recipe: RecipeListItem;
  deleting?: boolean;
  onDelete?: (recipe: RecipeListItem) => void;
}) {
  const status = recipeStatus(recipe);
  const { fontScale } = useWindowDimensions();
  const truncate = fontScale < 1.3;
  return (
    <View style={styles.card}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={[recipe.name, status.label, recipe.rating ? `${recipe.rating} von 5 Sternen` : '', recipe.user_verified ? 'Geprüft' : ''].filter(Boolean).join(', ')}
        accessibilityHint="Rezept öffnen"
        onPress={() => router.push(`/recipe/${recipe.id}`)}
        style={({ pressed }) => pressed && styles.pressed}>
        <Image
          accessible={false}
          source={{ uri: absoluteApiUrl(`/api/recipes/${recipe.id}/thumb?w=500`), headers: apiAuthHeaders() }}
          style={styles.image}
          contentFit="cover"
          cachePolicy="memory"
          transition={120}
        />
        <View style={styles.body}>
          <Text style={styles.name} numberOfLines={truncate ? 2 : undefined}>{recipe.name}</Text>
          {!!recipe.description && <Text style={styles.description} numberOfLines={truncate ? 2 : undefined}>{recipe.description}</Text>}
          <View style={styles.footer}>
            <Text style={styles.meta}>{[recipe.type, recipe.category].filter(Boolean).join(' · ')}</Text>
            {!!recipe.rating && <Text accessibilityLabel={`${recipe.rating} von 5 Sternen`} style={styles.rating}>{'★'.repeat(recipe.rating)}</Text>}
            {!!recipe.user_verified && <Text style={styles.verified}>✓ Geprüft</Text>}
            <Text style={[styles.status, { color: colors[status.tone] }]}>
              {status.label}
            </Text>
          </View>
        </View>
      </Pressable>
      {!!onDelete && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${recipe.name} in den Papierkorb verschieben`}
          disabled={deleting}
          accessibilityState={{ disabled: deleting, busy: deleting }}
          hitSlop={8}
          onPress={() => onDelete(recipe)}
          style={({ pressed }) => [
            styles.deleteButton,
            pressed && styles.deletePressed,
            deleting && styles.deleteDisabled,
          ]}>
          {deleting ? (
            <ActivityIndicator color={colors.danger} size="small" />
          ) : (
            <SymbolView name="trash" size={18} weight="semibold" tintColor={colors.danger} />
          )}
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    position: 'relative',
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.lg,
    backgroundColor: colors.surface,
  },
  pressed: { opacity: 0.8, transform: [{ scale: 0.99 }] },
  deleteButton: {
    position: 'absolute',
    top: 12,
    right: 12,
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 22,
    backgroundColor: colors.surface,
  },
  deletePressed: { opacity: 0.72, transform: [{ scale: 0.94 }] },
  deleteDisabled: { opacity: 0.55 },
  image: { width: '100%', aspectRatio: 16 / 10, backgroundColor: colors.border },
  body: { padding: 14, gap: 7 },
  name: { color: colors.text, fontSize: 19, lineHeight: 23, fontWeight: '800' },
  description: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  footer: { marginTop: space.xs, flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, justifyContent: 'space-between' },
  meta: { color: colors.muted, fontSize: 12, flex: 1 },
  rating: { color: colors.butterPressed, fontSize: 12, letterSpacing: -1 },
  verified: { color: colors.success, fontSize: 12, fontWeight: '800' },
  status: { fontSize: 12, fontWeight: '800' },
});
