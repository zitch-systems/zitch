import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import WaApprove from '@/app/(auth)/waapprove';

const mockApiJson = jest.fn();
const mockIcon = jest.fn();

jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), back: jest.fn() },
  useLocalSearchParams: () => ({ token: 'ap1.signed' }),
}));
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/pendingApproval', () => ({
  rememberWhatsAppApproval: jest.fn(async () => {}),
  clearPendingWhatsAppApproval: jest.fn(async () => {}),
}));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/AuthGuard', () => ({ __esModule: true, default: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => (props: { name: string }) => { mockIcon(props.name); return null; });
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#090', ink1: '#111', ink3: '#333', surface: '#fff', line: '#eee', lime: '#0c0', amber: '#fa0', red: '#f00' } }),
  font: { bold: 'bold', regular: 'regular', semibold: 'semibold', extrabold: 'extrabold' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, Text: RText, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null,
    Btn: (props: any) => R.createElement(Pressable, { ...props, accessibilityLabel: props.label }, R.createElement(RText, null, props.label)),
    PinPad: (props: any) => R.createElement(Pressable, { accessibilityLabel: 'pin', onPress: () => props.onComplete('1234') }),
  };
});

const texts = (tree: renderer.ReactTestRenderer) =>
  tree.root.findAllByType(Text).map((node) => [].concat(node.props.children).join(''));

async function approveWith(execute: Record<string, unknown>, preview: Record<string, unknown> = {}) {
  mockApiJson.mockImplementation(async (path: string) => (
    path.endsWith('/preview/')
      ? { success: true, summary: 'Send ₦5,000.00 to JOHN DOE', action_type: 'transfer', ...preview }
      : execute
  ));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<WaApprove />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  return tree;
}

beforeEach(() => {
  jest.clearAllMocks();
});

it('celebrates only a payment that actually went through', async () => {
  const tree = await approveWith({ success: true, outcome: 'success', message: '₦5,000.00 sent to JOHN DOE' });
  expect(texts(tree)).toContain('Payment approved');
  expect(mockIcon).toHaveBeenCalledWith('check');
});

it('does not show a refused payment as approved', async () => {
  const tree = await approveWith({ success: true, outcome: 'failed', message: '❌ Your transfer failed: declined.' });
  expect(texts(tree)).toContain('Payment not completed');
  expect(texts(tree)).not.toContain('Payment approved');
  expect(texts(tree)).toContain('❌ Your transfer failed: declined.');
});

it('says a still-settling payment is processing', async () => {
  const tree = await approveWith({ success: true, outcome: 'pending', message: '⏳ Still processing.' });
  expect(texts(tree)).toContain('Payment processing');
  expect(texts(tree)).not.toContain('Payment approved');
});

it('keeps the approved heading for a server that sends no outcome', async () => {
  const tree = await approveWith({ success: true, message: 'Done ✅' });
  expect(texts(tree)).toContain('Payment approved');
});

it('names an identity confirmation as one rather than a payment', async () => {
  const tree = await approveWith(
    { success: true, outcome: 'done', message: 'Unlocked ✅ - see the chat.' },
    { action_type: 'unlock', summary: "It's been a while - confirm it's you to continue" },
  );
  expect(texts(tree)).toContain('Identity confirmed');
  expect(texts(tree)).not.toContain('Payment approved');
});
