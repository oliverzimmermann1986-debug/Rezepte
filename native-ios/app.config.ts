import type { ExpoConfig } from 'expo/config';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const base = require('./app.json').expo as ExpoConfig;

type AppVariant = 'development' | 'preview' | 'production';

function appVariant(): AppVariant {
  const value = String(process.env.APP_VARIANT || 'production').toLowerCase();
  if (value === 'development' || value === 'preview') return value;
  return 'production';
}

export default (): ExpoConfig => {
  const variant = appVariant();
  const suffix = variant === 'production' ? '' : `.${variant === 'development' ? 'dev' : 'preview'}`;
  const bundleIdentifier = `de.mausbaeren.rezepte${suffix}`;

  return {
    ...base,
    name: variant === 'production'
      ? base.name
      : `${base.name} ${variant === 'development' ? 'Dev' : 'Preview'}`,
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
