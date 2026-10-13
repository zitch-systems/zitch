// Learn more https://docs.expo.dev/guides/customizing-metro
const { disableTypes } = require('image-size');
const { getDefaultConfig } = require('expo/metro-config');

// image-size <=2.0.2 can loop forever on malformed ICNS, HEIF/AVIF and JPEG-XL
// headers (GHSA-w3rx-r6r6-pgpr, GHSA-5p2g-fcmc-qvqq). Metro only reads assets
// from this repository and Zitch does not ship any of those formats, so remove
// the vulnerable parsers entirely until Metro supports image-size's patched
// 2.x API (its callable CommonJS 1.x import cannot use that major directly).
// scripts/test-image-size-mitigation.cjs verifies all four refusals in app CI.
// See docs/mobile-dependency-audit-2026-10-13.md for the remaining advisories;
// disabling these parsers does not make the whole dependency tree audit-clean.
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
