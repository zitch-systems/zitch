// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: [
      'android/**',
      'backend/**',
      'dist/**',
      'dist-audit/**',
      // Archived design-handoff prototypes are reference material, not shipped
      // application code. Keep production source and build scripts linted.
      'docs/design_handoff_v2/**',
      'docs/design_handoff_zitch_revamp/**',
      'ios/**',
      'landing/**',
      'landing-legacy/**',
      'node_modules/**',
      'zitch-meta-connector/**',
    ],
  },
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: {
      globals: {
        Buffer: 'readonly',
      },
    },
  },
]);
