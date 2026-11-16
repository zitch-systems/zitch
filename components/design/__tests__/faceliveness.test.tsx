import React from 'react';
import { Modal, Platform } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import FaceLivenessModal, { LIVENESS_URL, PAGE_TIMEOUT_MS, pickPictureSize } from '@/components/design/FaceLivenessModal';
import { EP } from '@/lib/endpoints';

const mockApiJson = jest.fn();
const mockTake = jest.fn();
const mockSizes = jest.fn();
const mockRequest = jest.fn();
let mockPermission: { granted: boolean; canAskAgain: boolean } = { granted: true, canAskAgain: true };
const mockGetBrightness = jest.fn();
const mockSetBrightness = jest.fn();
const mockRestoreBrightness = jest.fn();

jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('react-native-safe-area-context', () => {
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { SafeAreaView: View };
});
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', inkOnBrand: '#fff', ink1: '#111', ink2: '#222', ink3: '#333' } }),
  font: { bold: 'b', extrabold: 'x', regular: 'r', semibold: 's', medium: 'm' },
}));
jest.mock('@/components/configFiles/apiConfig', () => 'https://api.zitch.ng');
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
jest.mock('expo-camera', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const CameraView = R.forwardRef((props: { onCameraReady?: () => void }, ref) => {
    R.useImperativeHandle(ref, () => ({
      takePictureAsync: (...args: unknown[]) => mockTake(...args),
      getAvailablePictureSizesAsync: () => mockSizes(),
    }));
    R.useEffect(() => { props.onCameraReady?.(); }, []);
    return R.createElement(View, { ...props, testID: 'camera' });
  });
  return { CameraView, useCameraPermissions: () => [mockPermission, mockRequest] };
});

const open = async (props: Partial<React.ComponentProps<typeof FaceLivenessModal>> = {}) => {
  const onCapture = jest.fn();
  const onClose = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => {
    tree = renderer.create(<FaceLivenessModal visible onClose={onClose} onCapture={onCapture} {...props} />);
  });
  await act(async () => { tree.root.findByType(Modal).props.onShow(); });
  return { tree, onCapture, onClose };
};
const press = async (tree: renderer.ReactTestRenderer, label: string) => {
  await act(async () => { tree.root.findByProps({ accessibilityLabel: label, accessibilityRole: 'button' }).props.onPress(); });
};
const runClock = async (ms = 5000) => {
  for (let elapsed = 0; elapsed < ms; elapsed += 200) {
    await act(async () => { await jest.advanceTimersByTimeAsync(200); });
  }
};
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());
const web = (tree: renderer.ReactTestRenderer) => tree.root.findByProps({ testID: 'liveness-web' });
const say = (tree: renderer.ReactTestRenderer, data: object, url = LIVENESS_URL) =>
  act(async () => { web(tree).props.onMessage({ nativeEvent: { url, data: JSON.stringify(data) } }); });
/** The simple camera, as when the page reports it cannot run the check. */
const toCamera = async (tree: renderer.ReactTestRenderer) => {
  await say(tree, { type: 'unavailable', reason: 'model' });
};

beforeEach(() => {
  Platform.OS = 'ios';
  mockPermission = { granted: true, canAskAgain: true };
  mockApiJson.mockReset();
  mockRequest.mockReset().mockResolvedValue(mockPermission);
  mockGetBrightness.mockReset().mockResolvedValue(0.3);
  mockSetBrightness.mockReset().mockResolvedValue(undefined);
  mockRestoreBrightness.mockReset().mockResolvedValue(undefined);
  mockSizes.mockReset().mockResolvedValue(['4000x3000', '1920x1080', '1280x960', '640x480']);
  let shot = 0;
  mockTake.mockReset().mockImplementation(async () => ({ base64: `PHOTO${++shot}` }));
});
afterEach(() => { jest.useRealTimers(); });

// --- the guided check in the page -------------------------------------------
it('runs the liveness page from our API host, only that host', async () => {
  const { tree } = await open();
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

it('has our server check the frames from the moves and hands back the photo it chose', async () => {
  mockApiJson.mockResolvedValue({ success: true, frame: 1, liveness_token: 'PROOF' });
  const { tree, onCapture } = await open();
  await say(tree, { type: 'ready' });
  const capture = { type: 'capture', image: 'QUJD', frames: ['QUJD', 'REVG'], turns: ['R0hJ', 'SktM'] };
  // Only our page, and only well-formed frames.
  await say(tree, capture, 'https://evil.example/kyc/liveness/');
  await say(tree, { ...capture, frames: ['not base64!'] });
  await say(tree, { ...capture, frames: [] });
  expect(mockApiJson).not.toHaveBeenCalled();
  await say(tree, capture);
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaTier2FaceCheck,
    { frames: ['QUJD', 'REVG'], turns: ['R0hJ', 'SktM'] }, 90000);
  expect(onCapture).toHaveBeenCalledWith({ image: 'REVG', token: 'PROOF' });
  act(() => tree.unmount());
});

it('shows a refusal from the server and runs the moves again on a fresh page', async () => {
  mockApiJson.mockResolvedValueOnce({ success: false, code: 'liveness_no_turn',
    message: 'We didn’t see your head turn.', _httpStatus: 422 })
    .mockResolvedValueOnce({ success: true, frame: 0, liveness_token: 'PROOF-2' });
  const { tree, onCapture } = await open();
  await say(tree, { type: 'ready' });
  await say(tree, { type: 'capture', frames: ['QUJD'], turns: ['R0hJ'] });
  expect(text(tree)).toContain('We didn’t see your head turn.');
  expect(onCapture).not.toHaveBeenCalled();
  const before = web(tree);
  await press(tree, 'Try again');
  expect(web(tree)).not.toBe(before);                    // remounted: the page starts over
  expect(text(tree)).toContain('Getting the face check ready…');
  await say(tree, { type: 'ready' });
  await say(tree, { type: 'capture', frames: ['REVG'], turns: ['SktM'] });
  expect(onCapture).toHaveBeenCalledWith({ image: 'REVG', token: 'PROOF-2' });
  act(() => tree.unmount());
});

it('a page that sends no head-turn frames is not trusted: the camera check runs instead', async () => {
  const { tree, onCapture } = await open();
  await say(tree, { type: 'ready' });
  await say(tree, { type: 'capture', image: 'QUJD' });
  expect(mockApiJson).not.toHaveBeenCalled();
  expect(onCapture).not.toHaveBeenCalled();
  expect(tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => tree.unmount());
});

it('raises the screen to full brightness during the check and restores it after (iOS)', async () => {
  const { tree } = await open();
  await flush();
  expect(mockSetBrightness).toHaveBeenCalledWith(1);
  await act(async () => { tree.update(<FaceLivenessModal visible={false} onClose={jest.fn()} onCapture={jest.fn()} />); });
  await flush();
  expect(mockSetBrightness).toHaveBeenLastCalledWith(0.3);
  act(() => tree.unmount());
});

it('hands Android back to the system brightness when the check closes', async () => {
  Platform.OS = 'android';
  const { tree } = await open();
  await flush();
  expect(mockSetBrightness).toHaveBeenCalledWith(1);
  act(() => tree.unmount());
  await flush();
  expect(mockRestoreBrightness).toHaveBeenCalledTimes(1);
});

it('falls back to the camera check when the model or camera cannot run in the page', async () => {
  const { tree } = await open();
  await say(tree, { type: 'loading' });
  await say(tree, { type: 'unavailable', reason: 'model' });
  expect(tree.root.findAllByProps({ testID: 'liveness-web' })).toHaveLength(0);
  expect(tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  expect(tree.root.findByProps({ accessibilityLabel: 'Start face check' })).toBeTruthy();
  act(() => tree.unmount());
});

it('falls back to the camera check when the page never answers', async () => {
  jest.useFakeTimers();
  const { tree } = await open();
  await act(async () => { jest.advanceTimersByTime(PAGE_TIMEOUT_MS + 10); });
  expect(tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => tree.unmount());
});

it('falls back on a load error and offers the simple camera when the page says it is slow', async () => {
  const first = await open();
  await say(first.tree, { type: 'ready' });
  await say(first.tree, { type: 'slow' });
  await press(first.tree, 'Use the simple camera instead');
  expect(first.tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => first.tree.unmount());

  const second = await open();
  await act(async () => { web(second.tree).props.onError({ nativeEvent: {} }); });
  expect(second.tree.root.findByProps({ testID: 'camera' })).toBeTruthy();
  act(() => second.tree.unmount());
});

// --- the camera fallback -------------------------------------------------------
it('camera: counts down, takes two straight and three turned photos, and hands back the photo the server chose', async () => {
  jest.useFakeTimers();
  mockApiJson.mockResolvedValue({ success: true, frame: 1, liveness_token: 'PROOF' });
  const { tree, onCapture } = await open();
  await toCamera(tree);
  await press(tree, 'Start face check');
  expect(text(tree)).toContain('Look straight at the camera and hold still.');
  await runClock();
  expect(mockTake).toHaveBeenCalledTimes(5);
  for (const [options] of mockTake.mock.calls) {
    expect(options).toMatchObject({ base64: true, exif: false, shutterSound: false, skipProcessing: false });
  }
  expect(mockTake.mock.calls.map(([options]) => options.quality)).toEqual([0.85, 0.85, 0.5, 0.5, 0.5]);
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaTier2FaceCheck,
    { frames: ['PHOTO1', 'PHOTO2'], turns: ['PHOTO3', 'PHOTO4', 'PHOTO5'] }, 90000);
  expect(onCapture).toHaveBeenCalledWith({ image: 'PHOTO2', token: 'PROOF' });
  act(() => tree.unmount());
});

it('camera: never mirrors the photo and does not flash between frames', async () => {
  const { tree } = await open();
  await toCamera(tree);
  const camera = tree.root.findByProps({ testID: 'camera' });
  expect(camera.props.facing).toBe('front');
  expect(camera.props.mirror).toBeUndefined();
  expect(camera.props.animateShutter).toBe(false);
  act(() => tree.unmount());
});

it('camera: shows what to fix and lets the customer try again', async () => {
  jest.useFakeTimers();
  mockApiJson.mockResolvedValueOnce({ success: false, code: 'liveness_no_turn',
    message: 'We didn’t see your head turn.', _httpStatus: 422 })
    .mockResolvedValueOnce({ success: true, frame: 0, liveness_token: 'PROOF-2' });
  const { tree, onCapture } = await open();
  await toCamera(tree);
  await press(tree, 'Start face check');
  await runClock();
  expect(text(tree)).toContain('We didn’t see your head turn.');
  expect(onCapture).not.toHaveBeenCalled();
  await press(tree, 'Try again');
  await runClock();
  expect(onCapture).toHaveBeenCalledWith({ image: 'PHOTO6', token: 'PROOF-2' });
  act(() => tree.unmount());
});

it('camera: closing mid-check abandons it, with no upload and no result', async () => {
  jest.useFakeTimers();
  const { tree, onCapture, onClose } = await open();
  await toCamera(tree);
  await press(tree, 'Start face check');
  await act(async () => { await jest.advanceTimersByTimeAsync(900); });
  await press(tree, 'Close face check');
  expect(onClose).toHaveBeenCalled();
  await runClock();
  expect(mockTake).not.toHaveBeenCalled();
  expect(mockApiJson).not.toHaveBeenCalled();
  expect(onCapture).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('camera: an error becomes a retry, not a crash', async () => {
  jest.useFakeTimers();
  mockTake.mockRejectedValueOnce(new Error('busy'));
  const { tree, onCapture } = await open();
  await toCamera(tree);
  await press(tree, 'Start face check');
  await runClock();
  expect(text(tree)).toContain('We could not use the camera. Please try again.');
  expect(onCapture).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('camera: on Android asks for a photo near the bank size instead of the full sensor', async () => {
  Platform.OS = 'android';
  const { tree } = await open();
  await toCamera(tree);
  await flush();
  expect(tree.root.findByProps({ testID: 'camera' }).props.pictureSize).toBe('1280x960');
  act(() => tree.unmount());
});

it('picks a 4:3 size close to 1280 px', () => {
  expect(pickPictureSize(['4000x3000', '1920x1080', '1600x1200', '1280x960', '640x480'])).toBe('1280x960');
  expect(pickPictureSize(['4000x3000', '1920x1440', '1920x1080'])).toBe('1920x1440');
  expect(pickPictureSize(['4000x3000', '3264x2448'])).toBe('3264x2448');
  expect(pickPictureSize(['800x600', '640x480'])).toBe('800x600');
  expect(pickPictureSize(['Photo', 'High'])).toBeUndefined();
});
