import React from 'react';
import { Platform } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import FaceLivenessModal, { pickPictureSize } from '@/components/design/FaceLivenessModal';
import { EP } from '@/lib/endpoints';

const mockApiJson = jest.fn();
const mockTake = jest.fn();
const mockSizes = jest.fn();
let mockPermission = { granted: true, canAskAgain: true };
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: { children: React.ReactNode }) => children,
}));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', inkOnBrand: '#fff', ink1: '#111', ink2: '#222', ink3: '#333' } }),
  font: { bold: 'b', extrabold: 'x', regular: 'r', semibold: 's' },
}));
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
  return { CameraView, useCameraPermissions: () => [mockPermission, jest.fn()] };
});

const open = async (props: Partial<React.ComponentProps<typeof FaceLivenessModal>> = {}) => {
  const onCapture = jest.fn();
  const onClose = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => {
    tree = renderer.create(<FaceLivenessModal visible onClose={onClose} onCapture={onCapture} {...props} />);
  });
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
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());

beforeEach(() => {
  jest.useFakeTimers();
  Platform.OS = 'ios';
  mockPermission = { granted: true, canAskAgain: true };
  mockApiJson.mockReset();
  mockSizes.mockReset().mockResolvedValue(['4000x3000', '1920x1080', '1280x960', '640x480']);
  let shot = 0;
  mockTake.mockReset().mockImplementation(async () => ({ base64: `PHOTO-${++shot}` }));
});
afterEach(() => { jest.useRealTimers(); });

it('counts down, takes two straight and three turned photos, and hands back the photo the server chose', async () => {
  mockApiJson.mockResolvedValue({ success: true, frame: 1, liveness_token: 'PROOF' });
  const { tree, onCapture } = await open();
  await press(tree, 'Start face check');
  expect(text(tree)).toContain('Look straight at the camera and hold still.');
  await runClock();
  expect(mockTake).toHaveBeenCalledTimes(5);
  for (const [options] of mockTake.mock.calls) {
    expect(options).toMatchObject({ base64: true, exif: false, shutterSound: false, skipProcessing: false });
  }
  expect(mockTake.mock.calls.map(([options]) => options.quality)).toEqual([0.85, 0.85, 0.5, 0.5, 0.5]);
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaTier2FaceCheck,
    { frames: ['PHOTO-1', 'PHOTO-2'], turns: ['PHOTO-3', 'PHOTO-4', 'PHOTO-5'] }, 90000);
  expect(onCapture).toHaveBeenCalledWith({ image: 'PHOTO-2', token: 'PROOF' });
  act(() => tree.unmount());
});

it('the camera never mirrors the photo and does not flash between frames', async () => {
  const { tree } = await open();
  const camera = tree.root.findByProps({ testID: 'camera' });
  expect(camera.props.facing).toBe('front');
  expect(camera.props.mirror).toBeUndefined();
  expect(camera.props.animateShutter).toBe(false);
  act(() => tree.unmount());
});

it('shows what to fix and lets the customer try again', async () => {
  mockApiJson.mockResolvedValueOnce({ success: false, code: 'liveness_no_turn',
    message: 'We didn’t see your head turn.', _httpStatus: 422 })
    .mockResolvedValueOnce({ success: true, frame: 0, liveness_token: 'PROOF-2' });
  const { tree, onCapture } = await open();
  await press(tree, 'Start face check');
  await runClock();
  expect(text(tree)).toContain('We didn’t see your head turn.');
  expect(onCapture).not.toHaveBeenCalled();
  await press(tree, 'Try again');
  await runClock();
  expect(onCapture).toHaveBeenCalledWith({ image: 'PHOTO-6', token: 'PROOF-2' });
  act(() => tree.unmount());
});

it('closing mid-check abandons it: no upload and no result', async () => {
  const { tree, onCapture, onClose } = await open();
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

it('a camera error becomes a retry, not a crash', async () => {
  mockTake.mockRejectedValueOnce(new Error('busy'));
  const { tree, onCapture } = await open();
  await press(tree, 'Start face check');
  await runClock();
  expect(text(tree)).toContain('We could not use the camera. Please try again.');
  expect(onCapture).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('on Android asks the camera for a photo near the bank size instead of the full sensor', async () => {
  Platform.OS = 'android';
  const { tree } = await open();
  await act(async () => { await Promise.resolve(); });
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
