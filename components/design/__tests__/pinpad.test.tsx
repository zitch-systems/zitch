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
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    require('@/lib/biometrics').isBiometricTxnEnabled.mockResolvedValue(false);
    require('@/lib/biometrics').isBiometricAvailable.mockResolvedValue(false);
    require('@/lib/biometrics').biometricLabel.mockResolvedValue(null);
    require('@/lib/secureStore').hasTransactionPin.mockResolvedValue(false);
    require('@/lib/secureStore').getTransactionPin.mockResolvedValue(null);
  });
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
  it('ignores biometric approval that finishes after the sheet is dismissed', async () => {
    const bio = require('@/lib/biometrics');
    const store = require('@/lib/secureStore');
    bio.isBiometricTxnEnabled.mockResolvedValue(true);
    bio.isBiometricAvailable.mockResolvedValue(true);
    bio.biometricLabel.mockResolvedValue('fingerprint');
    store.hasTransactionPin.mockResolvedValue(true);
    let finish!: (pin: string) => void;
    store.getTransactionPin.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const onComplete = jest.fn();
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<PinPad onComplete={onComplete} />); });
    expect(store.getTransactionPin).toHaveBeenCalledTimes(1);
    act(() => tree.unmount());
    await act(async () => { finish('654321'); });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('does not race biometric approval against a queued manual PIN', async () => {
    const bio = require('@/lib/biometrics');
    const store = require('@/lib/secureStore');
    bio.isBiometricTxnEnabled.mockResolvedValue(true);
    bio.isBiometricAvailable.mockResolvedValue(true);
    bio.biometricLabel.mockResolvedValue('fingerprint');
    store.hasTransactionPin.mockResolvedValue(true);
    store.getTransactionPin.mockResolvedValue('654321');
    const onComplete = jest.fn();
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<PinPad onComplete={onComplete} autoBiometric={false} />); });
    for (const digit of ['1', '2', '3', '4', '5', '6']) {
      await act(async () => { tree.root.findByProps({ accessibilityLabel: `Digit ${digit}` }).props.onPress(); });
    }
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Use biometric approval' }).props.onPress(); });
    act(() => { jest.advanceTimersByTime(150); });
    expect(store.getTransactionPin).not.toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith('123456', false);
    act(() => tree.unmount());
  });


  describe('fingerprint first (18 Nov 2026)', () => {
    const fingerprintOn = () => {
      const bio = require('@/lib/biometrics');
      const store = require('@/lib/secureStore');
      bio.isBiometricTxnEnabled.mockResolvedValue(true);
      bio.isBiometricAvailable.mockResolvedValue(true);
      bio.biometricLabel.mockResolvedValue('fingerprint');
      store.hasTransactionPin.mockResolvedValue(true);
      store.getTransactionPin.mockResolvedValue(null);
      return store;
    };
    beforeEach(() => require('@/lib/payApproval').__resetPayApproval());

    it('opens on the fingerprint when it is on for payments', async () => {
      const store = fingerprintOn();
      let tree!: renderer.ReactTestRenderer;
      await act(async () => { tree = renderer.create(<PinPad onComplete={jest.fn()} />); });
      expect(tree.root.findAllByProps({ accessibilityLabel: 'Approve with fingerprint' }).length).toBeGreaterThan(0);
      expect(store.getTransactionPin).toHaveBeenCalledTimes(1);
      act(() => tree.unmount());
    });

    it('opens on the keypad after Use Payment PIN, with the fingerprint still a key away', async () => {
      const store = fingerprintOn();
      require('@/lib/payApproval').preferKeypadOnce();
      let tree!: renderer.ReactTestRenderer;
      await act(async () => { tree = renderer.create(<PinPad onComplete={jest.fn()} />); });
      expect(tree.root.findAllByProps({ accessibilityLabel: 'Digit 1' }).length).toBeGreaterThan(0);
      expect(tree.root.findAllByProps({ accessibilityLabel: 'Use biometric approval' }).length).toBeGreaterThan(0);
      expect(store.getTransactionPin).not.toHaveBeenCalled();
      act(() => tree.unmount());
      // only that one pad: the next opens on the fingerprint again
      await act(async () => { tree = renderer.create(<PinPad onComplete={jest.fn()} />); });
      expect(store.getTransactionPin).toHaveBeenCalledTimes(1);
      act(() => tree.unmount());
    });

    it('records a typed PIN, so the receipt can offer the fingerprint', async () => {
      const approval = require('@/lib/payApproval');
      const spy = jest.spyOn(approval, 'noteApproval');
      const onComplete = jest.fn();
      let tree!: renderer.ReactTestRenderer;
      await act(async () => { tree = renderer.create(<PinPad onComplete={onComplete} autoBiometric={false} />); });
      for (const digit of ['1', '2', '3', '4', '5', '6']) {
        await act(async () => { tree.root.findByProps({ accessibilityLabel: `Digit ${digit}` }).props.onPress(); });
      }
      act(() => { jest.advanceTimersByTime(150); });
      expect(onComplete).toHaveBeenCalledWith('123456', false);
      expect(spy).toHaveBeenLastCalledWith('pin');
      spy.mockRestore();
      act(() => tree.unmount());
    });

    it('records a scan as a scan', async () => {
      const store = fingerprintOn();
      store.getTransactionPin.mockResolvedValue('654321');
      const approval = require('@/lib/payApproval');
      const spy = jest.spyOn(approval, 'noteApproval');
      const onComplete = jest.fn();
      let tree!: renderer.ReactTestRenderer;
      await act(async () => { tree = renderer.create(<PinPad onComplete={onComplete} />); });
      expect(onComplete).toHaveBeenCalledWith('654321', true);
      expect(spy).toHaveBeenLastCalledWith('biometric');
      spy.mockRestore();
      act(() => tree.unmount());
    });
  });
});
