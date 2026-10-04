import type { RecipeListItem } from './types';

type RecipeStatus = { label: string; tone: 'success' | 'warning' | 'danger' };

export function recipeStatus(recipe: RecipeListItem): RecipeStatus {
  if (['pending', 'running'].includes(recipe.ingredients_status)) {
    return { label: '⏳ Zutaten werden ermittelt', tone: 'warning' };
  }
  if (recipe.ingredients_status === 'error') {
    return { label: '⚠ Auswertung fehlgeschlagen', tone: 'danger' };
  }
  if (recipe.ingredients_status === 'skipped') {
    return { label: '⚠ Beschreibung fehlt', tone: 'warning' };
  }
  if (recipe.ingredients_status === 'ok' && !recipe.needs_manual_care
      && recipe.ingredients_count > 0 && recipe.steps_count > 0) {
    return { label: '✓ Kochfertig', tone: 'success' };
  }
  return { label: '⚠ Manuell pflegen', tone: 'warning' };
}
