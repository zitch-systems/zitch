// Learn more https://docs.expo.dev/guides/customizing-metro
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

// react-native-svg's (unused) SVG WebView component has a type-only import of
// Node's `buffer`, which Metro cannot resolve. Stub it with an empty module —
// the import is erased at runtime so this has no effect on rendering.
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  buffer: require.resolve('./shims/empty.js'),
};

module.exports = config;
