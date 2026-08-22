import { Ingredient, RecipeStep } from './types';

let nextClientKey = 0;

function clientKey(kind: 'ingredient' | 'step') {
  nextClientKey += 1;
  return `${kind}-${nextClientKey}`;
}

export type EditableIngredient = Omit<Ingredient, 'amount'> & {
  amount?: number | string | null;
  clientKey: string;
};

export type EditableStep = Omit<RecipeStep, 'timer_seconds'> & {
  timer_seconds?: number | string | null;
  clientKey: string;
};

export function createIngredientRow(item: Ingredient = { name: '' }): EditableIngredient {
  return {
    ...item,
    amount: item.amount == null ? '' : String(item.amount),
    clientKey: clientKey('ingredient'),
  };
}

export function createStepRow(item: RecipeStep = { instruction: '' }): EditableStep {
  return {
    ...item,
    timer_seconds: item.timer_seconds == null ? '' : String(item.timer_seconds),
    clientKey: clientKey('step'),
  };
}
