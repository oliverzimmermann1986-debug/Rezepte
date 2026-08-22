export type RecipeListItem = {
  id: number;
  name: string;
  type?: string | null;
  category?: string | null;
  url?: string | null;
  thumb_filename?: string | null;
  description?: string;
  ingredients_status: 'pending' | 'running' | 'ok' | 'error' | 'skipped';
  ingredients_count: number;
  steps_count: number;
  needs_manual_care: boolean;
  is_favorite: boolean;
  rating: number;
  user_verified?: boolean | number;
  verified_at?: number | null;
  verified_by?: string | null;
  servings?: number | null;
};

export type Ingredient = {
  id?: number;
  name: string;
  amount?: number | null;
  unit?: string | null;
  raw?: string | null;
};

export type RecipeStep = {
  id?: number;
  instruction: string;
  timer_seconds?: number | null;
};

export type RecipeDetail = RecipeListItem & {
  ingredients: Ingredient[];
  steps: RecipeStep[];
  tags: { id: number; name: string; auto?: number }[];
  manual_care_reasons: string[];
  folder_path?: string;
  pdf_filename?: string | null;
  description_original?: string | null;
};

export type PendingSuggestion = {
  name?: string | null;
  type?: string | null;
  category?: string | null;
  confidence?: number | null;
  filename?: string | null;
  source?: string | null;
  platform?: string | null;
  ingredients?: Ingredient[];
  steps?: RecipeStep[];
  servings?: number | null;
};

export type PendingItem = {
  url: string;
  content_type?: string | null;
  description?: string | null;
  created_at?: number | null;
  status: string;
  reason?: string | null;
  ai_suggestion?: PendingSuggestion | null;
};

export type FailedDownload = {
  url: string;
  attempts: number;
  last_error?: string | null;
  first_seen?: number | null;
  last_try?: number | null;
};

export type CartItem = {
  id: number;
  name: string;
  amount?: number | null;
  unit?: string | null;
  checked: boolean;
};

export type RecurringCartItem = {
  id: number;
  name: string;
  amount?: number | null;
  default_unit?: string | null;
  category?: string | null;
  interval_days: number;
  next_due_on: string;
  due_in_days: number;
  active: boolean;
};

export type MealPlanItem = {
  id: number;
  recipe_id: number;
  recipe_name: string;
  planned_for: string;
  planned_servings: number;
};

export type MealPlanDay = {
  date: string;
  label: string;
  short_label: string;
  day_number: number;
  is_today: boolean;
  items: MealPlanItem[];
};

export type MealPlan = {
  week_start: string;
  previous_week: string;
  next_week: string;
  days: MealPlanDay[];
  summary: { planned_meals: number; planned_days: number; shopping_items: number };
};
