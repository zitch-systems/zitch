import React, { useState } from 'react';
import renderer, { act } from 'react-test-renderer';
import { TextInput } from 'react-native';
import { AmountField } from '../flowkit';

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink2: '#222' } }), font: {}, iconTint: () => '#eee' }));
jest.mock('@/components/design/Naira', () => ({ Naira: () => null, NText: () => null }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual('react'); const { TextInput } = jest.requireActual('react-native');
  return { Field: (props: any) => R.createElement(TextInput, props), Sheet: () => null, Btn: () => null, Money: () => null, money: String };
});
const Amount = () => { const [value, setValue] = useState('1000'); return <AmountField value={value} onChangeText={setValue} />; };

it('allows typing and backspacing across a thousands boundary without changing the value', () => {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<Amount />); });
  const field = () => tree.root.findByType(TextInput);
  expect(field().props.value).toBe('1,000');
  act(() => field().props.onFocus());
  expect(field().props.value).toBe('1000');
  act(() => field().props.onChangeText('10005'));
  expect(field().props.value).toBe('10005');
  act(() => field().props.onChangeText('1000'));
  expect(field().props.value).toBe('1000');
  act(() => field().props.onBlur());
  expect(field().props.value).toBe('1,000');
  act(() => tree.unmount());
});

it('preserves a pasted decimal and rejects signed or malformed amounts', () => {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<Amount />); });
  const field = () => tree.root.findByType(TextInput);
  act(() => field().props.onFocus());
  act(() => field().props.onChangeText('1,234.50'));
  expect(field().props.value).toBe('1234.50');
  for (const invalid of ['-100', '1e3', '12,34.50', '1.2.3']) {
    act(() => field().props.onChangeText(invalid));
    expect(field().props.value).toBe('1234.50');
  }
  act(() => field().props.onBlur());
  expect(field().props.value).toBe('1,234.50');
  act(() => tree.unmount());
});
