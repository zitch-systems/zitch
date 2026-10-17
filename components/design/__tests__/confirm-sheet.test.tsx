import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { Pressable } from 'react-native';
import { ConfirmSheet } from '../flowkit';
import { NText } from '../Naira';

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Loading', () => ({ Loading: () => null, LoadingMark: () => null }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

it('shows one titled close control, complete review values and both payment choices', () => {
  const close = jest.fn();
  const pay = jest.fn();
  const recipient = 'Ada Chinyere Okafor-Adekunle';
  const reference = 'REFERENCE-012345678901234567890123456789';
  const tree = renderer.create(<ConfirmSheet open onClose={close} title="Review transfer" total={1234.5} balance={5000}
    rows={[["Recipient", recipient], ["Reference", reference]]} onPay={pay} />);
  const closeButtons = tree.root.findAllByType(Pressable).filter(node => /^Close/.test(node.props.accessibilityLabel || ''));
  expect(closeButtons).toHaveLength(1);
  expect(closeButtons[0].props.accessibilityLabel).toBe('Close Review transfer');
  act(() => closeButtons[0].props.onPress());
  expect(close).toHaveBeenCalledTimes(1);
  for (const value of [recipient, reference]) {
    const text = tree.root.findAllByType(NText).find(node => node.props.children === value)!;
    expect(text).toBeDefined();
    expect(text.props.numberOfLines).toBeUndefined();
    expect(text.props.selectable).toBe(true);
  }
  act(() => tree.root.findByProps({ accessibilityLabel: 'Use payment PIN' }).props.onPress());
  expect(pay).toHaveBeenLastCalledWith(true);
  act(() => tree.root.findByProps({ accessibilityLabel: 'Pay ₦1,234.5' }).props.onPress());
  expect(pay).toHaveBeenLastCalledWith(false);
  act(() => tree.unmount());
});
