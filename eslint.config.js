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
      'docs/design_handoff_zitch_revamp/**',
      'ios/**',
      'landing/**',
      'landing-legacy/**',
      'node_modules/**',
    ],
  },
]);
