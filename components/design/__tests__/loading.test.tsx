import React from 'react';
import { AccessibilityInfo } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import { Loading, LoadingMark } from '@/components/design/Loading';

jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#000', ink3: '#666' } }),
  font: { medium: 'm', extrabold: 'x' },
}));

// The sprite sheet the coil plays: 84 frames in a 12 × 7 grid of 168 × 256 cells.
const COLS = 12;
const ROWS = 7;

const flat = (style: unknown): Record<string, any> =>
  Object.assign({}, ...(Array.isArray(style) ? style.flat(Infinity) : [style]).filter(Boolean));

// Host nodes only (the rendered native views), so wrapper components don't get in the way.
const hosts = (tree: renderer.ReactTestRenderer) => tree.root.findAll((n) => typeof n.type === 'string');
const sheetOf = (tree: renderer.ReactTestRenderer) => hosts(tree).find((n) => n.type === 'Image')!;
const cellOf = (tree: renderer.ReactTestRenderer) => hosts(tree).find((n) => flat(n.props.style).overflow === 'hidden')!;

async function mount(el: React.ReactElement) {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(el); });
  return tree;
}

describe('Loading (Zitch coil)', () => {
  beforeEach(() => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
    jest.spyOn(AccessibilityInfo, 'addEventListener').mockReturnValue({ remove: jest.fn() } as any);
  });
  afterEach(() => jest.restoreAllMocks());

  it('announces itself as a progress indicator with the given label', async () => {
    const tree = await mount(<Loading label="Signing you in…" />);
    const bar = tree.root.findByProps({ accessibilityRole: 'progressbar' });
    expect(bar.props.accessibilityLabel).toBe('Signing you in…');
    await act(async () => { tree.unmount(); });
  });

  it('shows the wordmark splash only when full-screen without a label', async () => {
    const splash = await mount(<Loading />);
    expect(JSON.stringify(splash.toJSON())).toContain('ZITCH');
    const inline = await mount(<Loading full={false} />);
    expect(JSON.stringify(inline.toJSON())).not.toContain('ZITCH');
    await act(async () => { splash.unmount(); inline.unmount(); });
  });

  it('plays the sprite sheet through a clipped cell, one whole frame at a time', async () => {
    const tree = await mount(<LoadingMark size={20} />);
    const sheet = flat(sheetOf(tree).props.style);
    const cell = flat(cellOf(tree).props.style);
    // the sheet is exactly COLS × ROWS cells, so every step lands on a frame boundary
    expect(sheet.width).toBeCloseTo(cell.width * COLS, 5);
    expect(sheet.height).toBeCloseTo(cell.height * ROWS, 5);
    expect(cell.width / cell.height).toBeCloseTo(168 / 256, 1);
    expect(sheet.transform).toHaveLength(2);
    // layout footprint stays the requested square
    const box = flat(tree.root.findByProps({ accessibilityRole: 'progressbar' }).props.style);
    expect(box).toMatchObject({ width: 20, height: 20 });
    await act(async () => { tree.unmount(); });
  });

  it('holds the still Z pose when the OS asks for reduced motion', async () => {
    (AccessibilityInfo.isReduceMotionEnabled as jest.Mock).mockResolvedValue(true);
    const tree = await mount(<LoadingMark />);
    expect(flat(sheetOf(tree).props.style).transform ?? []).toEqual([]);
    await act(async () => { tree.unmount(); });
  });
});
