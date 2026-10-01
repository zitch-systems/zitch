import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { PinPad } from '@/components/design/ui';

jest.mock('@/lib/biometrics', () => ({
  isBiometricTxnEnabled: jest.fn(async () => false),
  isBiometricAvailable: jest.fn(async () => false),
  biometricLabel: jest.fn(async () => null),
}));
jest.mock('@/lib/secureStore', () => ({
  getTransactionPin: jest.fn(async () => null),
  hasTransactionPin: jest.fn(async () => false),
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Loading', () => ({ Loading: () => null, LoadingMark: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({
    c: { brand: '#090', surface: '#fff', surface3: '#eee', line: '#ddd', ink1: '#111', ink3: '#333', red: '#f00' },
    theme: 'light',
  }),
  font: { bold: 'bold', medium: 'medium', regular: 'regular', semibold: 'semibold' },
  radius: { pill: 99 },
  ICON_COLORS: {},
  iconTint: (value: string) => value,
}));

describe('PinPad submission guard', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('fires once when the final digit is tapped twice before busy renders', async () => {
    const onComplete = jest.fn();
    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<PinPad onComplete={onComplete} autoBiometric={false} />);
      await Promise.resolve();
    });

    for (const digit of ['1', '2', '3', '4', '5']) {
      await act(async () => { tree.root.findByProps({ accessibilityLabel: `Digit ${digit}` }).props.onPress(); });
    }
    await act(async () => {
      const six = tree.root.findByProps({ accessibilityLabel: 'Digit 6' });
      six.props.onPress();
      six.props.onPress();
      jest.advanceTimersByTime(150);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith('123456', false);
  });

  it('does not submit after the PIN pad is dismissed during its completion delay', async () => {
    const onComplete = jest.fn();
    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<PinPad onComplete={onComplete} autoBiometric={false} />);
      await Promise.resolve();
    });

    for (const digit of ['1', '2', '3', '4', '5', '6']) {
      await act(async () => { tree.root.findByProps({ accessibilityLabel: `Digit ${digit}` }).props.onPress(); });
    }
    act(() => tree.unmount());
    act(() => { jest.advanceTimersByTime(150); });

    expect(onComplete).not.toHaveBeenCalled();
  });

  it('cancels a completed PIN when the user deletes before dispatch', async () => {
    const onComplete = jest.fn();
    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<PinPad onComplete={onComplete} autoBiometric={false} />);
      await Promise.resolve();
    });

    for (const digit of ['1', '2', '3', '4', '5', '6']) {
      await act(async () => { tree.root.findByProps({ accessibilityLabel: `Digit ${digit}` }).props.onPress(); });
    }
    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: 'Delete digit' }).props.onPress();
      jest.advanceTimersByTime(150);
    });

    expect(onComplete).not.toHaveBeenCalled();
  });
});
