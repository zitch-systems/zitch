import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { TextInput } from 'react-native';
import { PasteCode } from '@/components/design/PasteCode';
import { CodeBoxes } from '@/components/design/CodeBoxes';

const mockGetString = jest.fn();
const mockNotify = jest.fn();
jest.mock('expo-clipboard', () => ({ getStringAsync: () => mockGetString() }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', ink1: '#111', line: '#ddd', surface: '#fff' } }),
  font: { bold: 'b', extrabold: 'x', regular: 'r', semibold: 's' },
}));

beforeEach(() => { mockGetString.mockReset(); mockNotify.mockReset(); });

it('fills the code from a copied SMS', async () => {
  mockGetString.mockResolvedValue('Zitch: 482193 is your code. Do not share it.');
  const onCode = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<PasteCode onCode={onCode} />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Paste code' }).props.onPress(); });
  expect(onCode).toHaveBeenCalledWith('482193');
  expect(mockNotify).not.toHaveBeenCalled();
});

it('says so when the clipboard has no code', async () => {
  mockGetString.mockResolvedValue('hello');
  const onCode = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<PasteCode onCode={onCode} />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Paste code' }).props.onPress(); });
  expect(onCode).not.toHaveBeenCalled();
  expect(mockNotify).toHaveBeenCalledWith('No code to paste', expect.any(String), 'info');
});

it('code boxes take a whole pasted message (no maxLength cutting it first)', async () => {
  const onChange = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<CodeBoxes value="" onChange={onChange} accessibilityLabel="Code" />); });
  const input = tree.root.findByType(TextInput);
  expect(input.props.maxLength).toBeUndefined();
  await act(async () => { input.props.onChangeText('Your Zitch code is 482193. It expires in 10 minutes.'); });
  expect(onChange).toHaveBeenCalledWith('482193');
});
