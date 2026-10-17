import { light, dark } from '../theme';

jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

function luminance(hex: string) {
  const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function contrast(a: string, b: string) {
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (high + 0.05) / (low + 0.05);
}

describe.each([['light', light], ['dark', dark]] as const)('%s theme text readability', (_name, theme) => {
  it('keeps body, secondary, action and financial-state text readable on app surfaces', () => {
    for (const foreground of [theme.ink1, theme.ink2, theme.ink3, theme.brand, theme.red, theme.amber, theme.lime]) {
      for (const background of [theme.bg, theme.surface, theme.surface2, theme.surface3]) {
        expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  it('keeps primary button and hero text readable', () => {
    expect(contrast(theme.inkOnBrand, theme.brand)).toBeGreaterThanOrEqual(4.5);
    expect(contrast('#FFFFFF', theme.brandDeep)).toBeGreaterThanOrEqual(4.5);
    theme.heroGradient.forEach(color => expect(contrast('#FFFFFF', color)).toBeGreaterThanOrEqual(4.5));
  });
});
