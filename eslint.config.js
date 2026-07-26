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
      'ios/**',
      'landing/**',
      'landing-legacy/**',
      'node_modules/**',
    ],
  },
]);
