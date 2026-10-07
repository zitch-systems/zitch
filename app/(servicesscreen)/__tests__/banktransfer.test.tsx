import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import BankTransfer from '@/app/(servicesscreen)/banktransfer';

const mockPush = jest.fn();
const mockWallet = { accountNumber: '', accountName: 'Zitch/Ada', bankName: 'Bank', linked: [],
  fundingProvider: 'wema_vas', fundingMessage: 'Complete identity verification to set up your account.' };
const mockCopy = jest.fn();
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet }));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), push: (...args: unknown[]) => mockPush(...args) } }));
jest.mock('expo-clipboard', () => ({ setStringAsync: (...args: unknown[]) => mockCopy(...args) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' }, theme: 'light' }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
  radius: { pill: 20 }, iconTint: (value: string) => value,
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return { Screen: Container, Card: Container, NText: Text, Header: () => null,
    Btn: ({ label, onPress }: { label: string; onPress: () => void }) => ReactActual.createElement(Pressable, { accessibilityLabel: label, onPress }, ReactActual.createElement(Text, null, label)) };
});

beforeEach(() => { mockWallet.accountNumber = ''; mockCopy.mockReset(); mockPush.mockReset(); });

it.each(['', '7111234567', '123'])('shows setup instead of funding actions for unavailable number %s', async (number) => {
  mockWallet.accountNumber = number;
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BankTransfer />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Your funding account is not available yet');
  expect(output).toContain(mockWallet.fundingMessage);
  expect(output).not.toContain('Copy Number');
  expect(output).not.toContain('Tap a bank to open its app');
  act(() => tree.root.findByProps({ accessibilityLabel: 'Check account setup' }).props.onPress());
  expect(mockPush).toHaveBeenCalledWith('/addmoney');
  expect(mockCopy).not.toHaveBeenCalled();
  await act(async () => tree.unmount());
});

it('copies the exact live funding number without promising unrestricted instant funding', async () => {
  mockWallet.accountNumber = '7121234567';
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BankTransfer />); });
  expect(JSON.stringify(tree.toJSON())).toContain('712 123 4567');
  expect(JSON.stringify(tree.toJSON())).not.toMatch(/any amount|instantly/);
  const copy = tree.root.findAllByProps({ accessibilityLabel: 'Copy Number' }).find((node) => typeof node.type !== 'string')!;
  await act(async () => { await copy.props.onPress(); });
  expect(mockCopy).toHaveBeenCalledWith('7121234567');
  await act(async () => tree.unmount());
});
