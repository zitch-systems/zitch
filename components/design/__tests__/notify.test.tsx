import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { FLASH_MS, flash, notify, NotifyHost } from '@/components/design/Notify';

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    lime: '#090', red: '#f00', brand: '#099', surface: '#fff',
    line: '#ddd', ink1: '#111', ink3: '#333', inkOnBrand: '#fff',
  } }),
  font: { extrabold: 'extrabold', regular: 'regular', bold: 'bold' },
}));

let hosts: renderer.ReactTestRenderer[];

function mountHost() {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<NotifyHost />); });
  hosts.push(tree);
  return tree;
}

beforeEach(() => {
  hosts = [];
  jest.useFakeTimers();
});

afterEach(() => {
  act(() => hosts.forEach((host) => host.unmount()));
  jest.useRealTimers();
});

it('keeps the replacement host registered when the older root unmounts', () => {
  const older = mountHost();
  const current = mountHost();

  act(() => older.unmount());
  act(() => notify('Error', 'Email or phone cannot be empty'));

  expect(JSON.stringify(current.toJSON())).toContain('Email or phone cannot be empty');
});

it('keeps normal notifications open until the user dismisses them', () => {
  const host = mountHost();
  act(() => notify('Error', 'Email or phone cannot be empty'));
  act(() => jest.advanceTimersByTime(FLASH_MS * 2));
  expect(JSON.stringify(host.toJSON())).toContain('Email or phone cannot be empty');

  act(() => host.root.findByProps({ accessibilityLabel: 'Close notification' }).props.onPress());
  expect(host.toJSON()).toBeNull();
});

it('gives a replacement flash its full display time', () => {
  const host = mountHost();
  act(() => flash('Saved', 'First confirmation'));
  act(() => jest.advanceTimersByTime(FLASH_MS - 100));
  act(() => flash('Saved', 'Second confirmation'));
  act(() => jest.advanceTimersByTime(100));
  expect(JSON.stringify(host.toJSON())).toContain('Second confirmation');
  expect(host.root.findAllByProps({ accessibilityLabel: 'Close notification' })).toHaveLength(0);

  act(() => jest.advanceTimersByTime(FLASH_MS - 100));
  expect(host.toJSON()).toBeNull();
});
