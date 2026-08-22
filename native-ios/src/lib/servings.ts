export const MIN_COOK_SERVINGS = 1;
export const MAX_COOK_SERVINGS = 50;

export function normalizedServings(value?: number | null) {
  if (!value || !Number.isFinite(value) || value < MIN_COOK_SERVINGS) return null;
  return Math.min(MAX_COOK_SERVINGS, Math.round(value));
}

export function portionLabel(value: number) {
  return `${value} ${value === 1 ? 'Portion' : 'Portionen'}`;
}

export function formatScaledAmount(
  value: number | null | undefined,
  multiplier: number,
) {
  if (value === null || value === undefined) return '–';
  const rounded = Math.round(value * multiplier * 100) / 100;
  return Number.isInteger(rounded)
    ? String(rounded)
    : String(rounded).replace('.', ',');
}
