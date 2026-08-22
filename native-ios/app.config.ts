import type { ConfigContext, ExpoConfig } from 'expo/config';

type AppVariant = 'development' | 'preview' | 'production';

function appVariant(): AppVariant {
  const value = String(process.env.APP_VARIANT || 'production').toLowerCase();
  if (value === 'development' || value === 'preview') return value;
  return 'production';
}

export default ({ config: base }: ConfigContext): ExpoConfig => {
  const variant = appVariant();
  const suffix = variant === 'production' ? '' : `.${variant === 'development' ? 'dev' : 'preview'}`;
  const bundleIdentifier = `de.mausbaeren.rezepte${suffix}`;
  const baseName = base.name || 'Rezepte';

  return {
    ...base,
    slug: base.slug || 'rezepte-ios',
    name: variant === 'production'
      ? baseName
      : `${baseName} ${variant === 'development' ? 'Dev' : 'Preview'}`,
    ios: {
      ...base.ios,
      bundleIdentifier,
    },
    extra: {
      ...base.extra,
      appVariant: variant,
      keychainService: bundleIdentifier,
    },
  };
};
