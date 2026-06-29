// https://docs.expo.dev/guides/using-eslint/
//
// eslint-config-expo gives the base rules, but the project needs the TS parser,
// the RN/Jest/Node environments and the new-JSX-runtime settings applied
// explicitly — otherwise `no-undef` / `react/jsx-no-undef` fire ~1.1k false
// positives on TS types, JSX components, `__DEV__` and `__dirname`. TypeScript
// already checks for undefined identifiers, so the core `no-undef` is disabled
// for this codebase (the standard guidance with @typescript-eslint).
module.exports = {
  root: true,
  extends: ['expo'],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  env: { browser: true, node: true, es2021: true, jest: true },
  settings: { react: { version: 'detect' } },
  globals: { __DEV__: 'readonly', React: 'readonly' },
  rules: {
    // TypeScript is the source of truth for undefined identifiers/types.
    'no-undef': 'off',
    // New JSX transform — React need not be in scope.
    'react/react-in-jsx-scope': 'off',
    // Prefer the TS-aware unused-vars; allow intentional _-prefixed throwaways.
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    'react-hooks/exhaustive-deps': 'warn',
  },
  // docs/ holds the in-browser-Babel design-handoff prototypes (reference only,
  // not shipped) and landing/ + mcp-server/ are separate sub-projects.
  ignorePatterns: ['node_modules/', 'dist/', '.expo/', 'landing/', 'backend/', 'coverage/', 'docs/', 'mcp-server/'],
};
