import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FlatList, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { StateView } from '@/components/ui';
import { colors, radii, space } from '@/constants/design';
import { api } from '@/lib/api';

type DuplicateRecipe = {
  id: number;
  name: string;
  type?: string | null;
  category?: string | null;
  url?: string | null;
  folder_path?: string | null;
};

type AuditGroup = {
  name?: string;
  url?: string;
  folder?: string;
  names?: string[];
  items: DuplicateRecipe[];
};

type SimilarGroup = Partial<AuditGroup> & { warning?: string };

type DuplicateAudit = {
  total_recipes: number;
  exact_duplicates: AuditGroup[];
  url_duplicates: AuditGroup[];
  folder_duplicates: AuditGroup[];
  similar_clusters: SimilarGroup[];
  audit_meta?: { similarity_partial?: boolean };
};

type DisplayGroup = {
  key: string;
  kind: 'Name' | 'Link' | 'Ordner' | 'Ähnlich';
  title: string;
  detail: string;
  items: DuplicateRecipe[];
};

function compactSource(value?: string | null) {
  if (!value) return '';
  try {
    const parsed = new URL(value);
    return `${parsed.hostname.replace(/^www\./, '')}${parsed.pathname}`;
  } catch {
    return value;
  }
}

function groupKey(kind: string, items: DuplicateRecipe[]) {
  return `${kind}:${items.map(item => item.id).sort((a, b) => a - b).join('-')}`;
}

function buildGroups(result: DuplicateAudit): DisplayGroup[] {
  return [
    ...result.url_duplicates.map(group => ({
      key: groupKey('url', group.items),
      kind: 'Link' as const,
      title: 'Gleicher Quellenlink',
      detail: compactSource(group.url),
      items: group.items,
    })),
    ...result.exact_duplicates.map(group => ({
      key: groupKey('name', group.items),
      kind: 'Name' as const,
      title: 'Gleicher Rezeptname',
      detail: group.items[0]?.name || group.name || 'Unbekannt',
      items: group.items,
    })),
    ...result.similar_clusters
      .filter(group => !group.warning && (group.items?.length || 0) > 1)
      .map(group => ({
        key: groupKey('similar', group.items || []),
        kind: 'Ähnlich' as const,
        title: 'Ähnliche Rezeptnamen',
        detail: (group.names || []).join(' · '),
        items: group.items || [],
      })),
    ...result.folder_duplicates.map(group => ({
      key: groupKey('folder', group.items),
      kind: 'Ordner' as const,
      title: 'Gleicher Speicherordner',
      detail: group.folder || 'Ordnerkonflikt',
      items: group.items,
    })),
  ];
}

export function AdminDuplicates({
  visible,
  onClose,
  onOpenRecipe,
}: {
  visible: boolean;
  onClose: () => void;
  onOpenRecipe: (recipeId: number) => void;
}) {
  const [audit, setAudit] = useState<DuplicateAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams({ similarity: '0.88' });
      if (refresh) params.set('refresh', 'true');
      setAudit(await api<DuplicateAudit>(`/api/audit?${params}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Dubletten konnten nicht geprüft werden');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) void load(false);
  }, [load, visible]);

  const groups = useMemo(() => audit ? buildGroups(audit) : [], [audit]);
  const candidateCount = useMemo(
    () => new Set(groups.flatMap(group => group.items.map(item => item.id))).size,
    [groups],
  );

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={['top', 'bottom', 'left', 'right']}>
        <View style={styles.header}>
          <Pressable accessibilityRole="button" onPress={onClose} style={styles.headerAction}>
            <Text style={styles.headerLink}>Fertig</Text>
          </Pressable>
          <Text style={styles.title}>Dubletten</Text>
          <Pressable accessibilityRole="button" onPress={() => void load(true)} disabled={loading} style={styles.headerAction}>
            <Text style={[styles.headerLink, styles.right, loading && styles.disabled]}>Neu prüfen</Text>
          </Pressable>
        </View>

        {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
        {loading && !audit ? (
          <StateView title="Rezeptbestand wird verglichen" loading />
        ) : (
          <FlatList
            data={groups}
            keyExtractor={group => group.key}
            contentContainerStyle={styles.content}
            ListHeaderComponent={audit ? (
              <View style={styles.intro}>
                <Text style={styles.introTitle}>{groups.length} Gruppen · {candidateCount} Rezepte</Text>
                <Text style={styles.introText}>
                  Gleiche Links und Namen sind starke Hinweise. Ähnliche Namen sind nur Vorschläge und müssen verglichen werden. Es wird nichts automatisch gelöscht.
                </Text>
                {!!audit.audit_meta?.similarity_partial && (
                  <Text style={styles.warning}>Die Ähnlichkeitssuche wurde aus Zeitgründen nur teilweise ausgeführt.</Text>
                )}
              </View>
            ) : null}
            renderItem={({ item: group }) => (
              <View style={styles.group}>
                <View style={styles.groupHeader}>
                  <Text style={styles.kind}>{group.kind}</Text>
                  <Text style={styles.groupCount}>{group.items.length} Treffer</Text>
                </View>
                <Text style={styles.groupTitle}>{group.title}</Text>
                <Text style={styles.groupDetail} numberOfLines={2}>{group.detail}</Text>
                <View style={styles.recipes}>
                  {group.items.map(recipe => (
                    <Pressable
                      key={recipe.id}
                      accessibilityRole="button"
                      accessibilityLabel={`${recipe.name}, Rezept ${recipe.id} öffnen`}
                      onPress={() => onOpenRecipe(recipe.id)}
                      style={({ pressed }) => [styles.recipe, pressed && styles.pressed]}>
                      <View style={styles.recipeText}>
                        <Text style={styles.recipeName} numberOfLines={2}>{recipe.name}</Text>
                        <Text style={styles.recipeMeta} numberOfLines={1}>
                          {[recipe.type, recipe.category, `ID ${recipe.id}`].filter(Boolean).join(' · ')}
                        </Text>
                        {!!recipe.url && <Text style={styles.recipeSource} numberOfLines={1}>{compactSource(recipe.url)}</Text>}
                      </View>
                      <Text style={styles.chevron}>›</Text>
                    </Pressable>
                  ))}
                </View>
              </View>
            )}
            ItemSeparatorComponent={() => <View style={{ height: 12 }} />}
            ListEmptyComponent={audit ? (
              <StateView title="Keine Dubletten gefunden" message={`${audit.total_recipes} Rezepte wurden geprüft.`} />
            ) : null}
          />
        )}
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  header: { minHeight: 56, paddingHorizontal: space.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  headerAction: { minWidth: 82, minHeight: 44, justifyContent: 'center' },
  headerLink: { color: colors.text, fontSize: 15, fontWeight: '800' },
  right: { textAlign: 'right' },
  disabled: { opacity: 0.4 },
  title: { color: colors.text, fontSize: 17, fontWeight: '900' },
  error: { margin: space.md, marginBottom: 0, padding: 12, color: colors.danger, backgroundColor: colors.dangerSurface, borderRadius: radii.sm },
  content: { padding: space.md, paddingBottom: 60 },
  intro: { gap: 7, paddingBottom: space.md },
  introTitle: { color: colors.text, fontSize: 22, fontWeight: '900' },
  introText: { color: colors.muted, lineHeight: 20 },
  warning: { color: colors.warning, lineHeight: 19, padding: 10, borderRadius: radii.sm, backgroundColor: colors.warningSurface },
  group: { padding: 14, gap: 7, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.surface },
  groupHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  kind: { color: colors.warning, fontSize: 11, letterSpacing: 1.1, fontWeight: '900', textTransform: 'uppercase' },
  groupCount: { color: colors.muted, fontSize: 12, fontWeight: '700' },
  groupTitle: { color: colors.text, fontSize: 18, fontWeight: '900' },
  groupDetail: { color: colors.muted, lineHeight: 18 },
  recipes: { gap: 7, paddingTop: 4 },
  recipe: { minHeight: 66, padding: 11, flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: radii.sm, backgroundColor: colors.cream },
  recipeText: { flex: 1, gap: 3 },
  recipeName: { color: colors.text, fontSize: 15, fontWeight: '900' },
  recipeMeta: { color: colors.muted, fontSize: 12 },
  recipeSource: { color: colors.success, fontSize: 11 },
  chevron: { color: colors.muted, fontSize: 25 },
  pressed: { opacity: 0.7 },
});
