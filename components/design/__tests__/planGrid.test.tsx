import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { PlanGrid, PLANS_PER_ROW } from '../billkit';

jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Naira', () => ({ Naira: () => null }));
jest.mock('@/components/design/ui', () => ({ money: (v: number) => `₦${v}` }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { surface: '#fff', surface2: '#f4f9f8', brand: '#066E66', inkOnBrand: '#fff', ink1: '#000', ink3: '#526863', line: '#E2EEEB' }, theme: 'light' }),
  font: { regular: 'r', medium: 'm', bold: 'b', extrabold: 'x' },
}));

// 17 Nov 2026: "these tabs are too big, optmise them" — the data plans go three across.
it('lays data plans three across, compact, and picks one on tap', () => {
  const onPick = jest.fn();
  const plans = ['1GB', '2GB', '5GB', '10GB'].map((label, i) => ({ id: String(i), label, sub: '30 days', price: 350 * (i + 1) }));
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<PlanGrid plans={plans} value="2" onPick={onPick} />); });
  expect(PLANS_PER_ROW).toBe(3);
  const all = tree.root.findAll((n) => n.props.accessibilityRole === 'radio' && typeof n.props.onPress === 'function' && n.props.style && n.props.style.paddingVertical != null);
  const tiles = all.filter((n, i) => all.findIndex((m) => m.props.accessibilityLabel === n.props.accessibilityLabel) === i);
  expect(tiles).toHaveLength(4);
  const style = tiles[0].props.style;
  expect(style.paddingVertical).toBeLessThanOrEqual(10);
  // the first row holds three plans; the second row holds the fourth and two spacers
  const rows = tree.root.findAll((n) => n.props.style && n.props.style.flexDirection === 'row' && n.props.style.gap === 8 && typeof n.type === 'string');
  expect(rows.length).toBeGreaterThanOrEqual(2);
  act(() => tiles[3].props.onPress());
  expect(onPick).toHaveBeenCalledWith('3');
  act(() => tree.unmount());
});
