import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { BalanceHint } from '@/components/design/flowkit';

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { red: '#c00', brand: '#090', ink2: '#222', ink3: '#333' } }),
  font: { bold: 'bold', semibold: 'semibold', regular: 'regular' },
  iconTint: () => '#eee',
}));
jest.mock('@/components/design/Naira', () => {
  const { Text: RText } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Naira: () => null, NText: ({ children }: { children: ReactNode }) => <RText>{children}</RText> };
});
jest.mock('@/components/design/ui', () => ({
  Sheet: () => null, Btn: () => null, Money: () => null, Field: () => null,
  money: (value: number) => `₦${value}`,
}));

const words = (element: React.ReactElement) => {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(element); });
  return JSON.stringify(tree.toJSON());
};

it('names the minimum when the amount is below it', () => {
  expect(words(<BalanceHint amount={20} balance={1000} min={50} />)).toContain('Minimum amount is');
  expect(words(<BalanceHint amount={20} balance={1000} min={50} />)).toContain('₦50');
});

it('falls back to the balance line at or above the minimum', () => {
  const out = words(<BalanceHint amount={50} balance={1000} min={50} />);
  expect(out).not.toContain('Minimum amount is');
  expect(out).toContain('Balance');
});

it('stays silent about a minimum before an amount is entered', () => {
  expect(words(<BalanceHint amount={0} balance={1000} min={50} />)).not.toContain('Minimum amount is');
});
