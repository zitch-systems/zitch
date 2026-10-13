// Learn more https://docs.expo.dev/guides/customizing-metro
const { disableTypes } = require('image-size');
const { getDefaultConfig } = require('expo/metro-config');

// image-size 2.0.4 fixes the parser advisories. The guarded postinstall adapter
// connects Metro 0.80's Buffer/file calls to the maintained parser exports. Keep unused
// ICNS/HEIF/JPEG-XL formats disabled as an additional boundary; app CI checks it.
disableTypes(['icns', 'heif', 'jxl', 'jxl-stream']);

const config = getDefaultConfig(__dirname);

// react-native-svg's (unused) SVG WebView component has a type-only import of
// Node's `buffer`, which Metro cannot resolve. Stub it with an empty module —
// the import is erased at runtime so this has no effect on rendering.
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  buffer: require.resolve('./shims/empty.js'),
};

module.exports = config;
