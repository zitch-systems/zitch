import React from 'react';
import { Modal, Platform } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import FaceLivenessModal, { LIVENESS_URL, PAGE_TIMEOUT_MS } from '@/components/design/FaceLivenessModal';

const mockRequest = jest.fn();
let mockPermission: { granted: boolean; canAskAgain: boolean } = { granted: true, canAskAgain: true };
const mockGetBrightness = jest.fn();
const mockSetBrightness = jest.fn();
const mockRestoreBrightness = jest.fn();

jest.mock('expo-camera', () => ({
  useCameraPermissions: () => [mockPermission, mockRequest],
  CameraView: (props: object) => {
    const R = jest.requireActual<typeof import('react')>('react');
    const { View } = jest.requireActual<typeof import('react-native')>('react-native');
    return R.createElement(View, { ...props, testID: 'camera' });
  },
}));
jest.mock('expo-brightness', () => ({
  getBrightnessAsync: () => mockGetBrightness(),
  setBrightnessAsync: (value: number) => mockSetBrightness(value),
  restoreSystemBrightnessAsync: () => mockRestoreBrightness(),
}));
jest.mock('react-native-webview', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { WebView: (props: object) => R.createElement(View, props) };
});
jest.mock('react-native-safe-area-context', () => {
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { SafeAreaView: View };
});
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink3: '#333', lime: '#0f0', inkOnBrand: '#fff' } }),
  font: { bold: 'b', regular: 'r', medium: 'm' },
}));
jest.mock('@/components/configFiles/apiConfig', () => 'https://api.zitch.ng');

const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

async function mount(props: Partial<React.ComponentProps<typeof FaceLivenessModal>> = {}) {
  const onCapture = jest.fn();
  const onClose = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => {
    tree = renderer.create(<FaceLivenessModal visible onClose={onClose} onCapture={onCapture} {...props} />);
  });
  await act(async () => { tree.root.findByType(Modal).props.onShow(); });
  return { tree, onCapture, onClose };
}

const web = (tree: renderer.ReactTestRenderer) => tree.root.findByProps({ testID: 'liveness-web' });
const say = (tree: renderer.ReactTestRenderer, data: object, url = LIVENESS_URL) =>
  act(async () => { web(tree).props.onMessage({ nativeEvent: { url, data: JSON.stringify(data) } }); });

beforeEach(() => {
  Platform.OS = 'ios';
  mockPermission = { granted: true, canAskAgain: true };
  mockRequest.mockReset().mockResolvedValue(mockPermission);
  mockGetBrightness.mockReset().mockResolvedValue(0.3);
  mockSetBrightness.mockReset().mockResolvedValue(undefined);
  mockRestoreBrightness.mockReset().mockResolvedValue(undefined);
});
afterEach(() => { jest.useRealTimers(); });

it('runs the liveness page from our API host, only that host', async () => {
  const { tree } = await mount();
  expect(LIVENESS_URL).toBe('https://api.zitch.ng/kyc/liveness/');
  const view = web(tree);
  expect(view.props.source).toEqual({ uri: 'https://api.zitch.ng/kyc/liveness/' });
  expect(view.props.mediaCapturePermissionGrantType).toBe('grantIfSameHostElsePrompt');
  expect(view.props.allowsInlineMediaPlayback).toBe(true);
  expect(view.props.onShouldStartLoadWithRequest({ url: 'https://api.zitch.ng/kyc/liveness/' })).toBe(true);
  expect(view.props.onShouldStartLoadWithRequest({ url: 'https://evil.example/kyc/liveness/' })).toBe(false);
  expect(tree.root.findAllByProps({ testID: 'camera' })).toHaveLength(0);
  act(() => tree.unmount());
});

it('raises the screen to full brightness during the check and restores it after (iOS)', async () => {
  const { tree } = await mount();
  await flush();
  expect(mockSetBrightness).toHaveBeenCalledWith(1);
  await act(async () => { tree.update(<FaceLivenessModal visible={false} onClose={jest.fn()} onCapture={jest.fn()} />); });
  await flush();
  expect(mockSetBrightness).toHaveBeenLastCalledWith(0.3);
  act(() => tree.unmount());
});

it('hands Android back to the system brightness when the check closes', async () => {
  Platform.OS = 'android';
  const { tree } = await mount();
  await flush();
  expect(mockSetBrightness).toHaveBeenCalledWith(1);
  act(() => tree.unmount());
  await flush();
  expect(mockRestoreBrightness).toHaveBeenCalledTimes(1);
});

it('passes the frame from the moves to onCapture, from our page only', async () => {
  const { tree, onCapture } = await mount();
  await say(tree, { type: 'ready' });
  await say(tree, { type: 'capture', image: 'QUJD' }, 'https://evil.example/kyc/liveness/');
  await say(tree, { type: 'capture', image: 'not base64!' });
  expect(onCapture).not.toHaveBeenCalled();
  await say(tree, { type: 'capture', image: 'QUJD' });
  expect(onCapture).toHaveBeenCalledWith('QUJD');
  act(() => tree.unmount());
});

it('falls back to the plain camera when the model or camera cannot run in the page', async () => {
  const { tree } = await mount();
  await say(tree, { type: 'loading' });
  await say(tree, { type: 'unavailable', reason: 'model' });
  expect(tree.root.findAllByProps({ testID: 'liveness-web' })).toHaveLength(0);
  expect(tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  expect(tree.root.findByProps({ accessibilityLabel: 'Take selfie' })).toBeTruthy();
  act(() => tree.unmount());
});

it('falls back to the plain camera when the page never answers', async () => {
  jest.useFakeTimers();
  const { tree } = await mount();
  await act(async () => { jest.advanceTimersByTime(PAGE_TIMEOUT_MS + 10); });
  expect(tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => tree.unmount());
});

it('falls back on a load error and offers the simple camera when the page says it is slow', async () => {
  const first = await mount();
  await say(first.tree, { type: 'ready' });
  await say(first.tree, { type: 'slow' });
  await act(async () => { first.tree.root.findByProps({ accessibilityLabel: 'Use the simple camera instead' }).props.onPress(); });
  expect(first.tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => first.tree.unmount());

  const second = await mount();
  await act(async () => { web(second.tree).props.onError({ nativeEvent: {} }); });
  expect(second.tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => second.tree.unmount());
});
