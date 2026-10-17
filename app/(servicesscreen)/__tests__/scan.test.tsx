import React from 'react';
import { Linking, Platform } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Scan from '../scan';
import BizPayment from '../bizpayment';

let mockPermission = { granted: true, canAskAgain: true };
const mockRequest = jest.fn();
const mockRefresh = jest.fn();
const mockReplace = jest.fn();
const mockPush = jest.fn();
const mockNotify = jest.fn();
jest.mock('expo-camera', () => ({
  useCameraPermissions: () => [mockPermission, mockRequest, mockRefresh],
  CameraView: (props: any) => { const R = jest.requireActual('react'); const { View } = jest.requireActual('react-native'); return R.createElement(View, { ...props, testID: 'camera' }); },
}));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: (...args: unknown[]) => mockReplace(...args), push: (...args: unknown[]) => mockPush(...args) } }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111', ink2: '#222', ink3: '#333' } }), font: { regular: 'r', bold: 'b' } }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual('react');
  const { View, TextInput, Pressable } = jest.requireActual('react-native');
  const Container = ({ children }: any) => R.createElement(View, null, children);
  return {
    Screen: Container, Card: Container, Header: () => null, HeaderLink: () => null,
    Field: ({ label, ...props }: any) => R.createElement(TextInput, { ...props, accessibilityLabel: label }),
    Btn: ({ label, ...props }: any) => R.createElement(Pressable, { ...props, accessibilityLabel: label }),
  };
});
const control = (tree: renderer.ReactTestRenderer, label: string) => tree.root.findByProps({ accessibilityLabel: label });

beforeEach(() => {
  Platform.OS = 'ios'; mockPermission = { granted: true, canAskAgain: true };
  mockRequest.mockReset().mockResolvedValue(mockPermission); mockRefresh.mockReset().mockResolvedValue(mockPermission);
  mockReplace.mockReset(); mockPush.mockReset(); mockNotify.mockReset();
});
afterEach(() => jest.restoreAllMocks());

it('opens phone settings for permanently denied camera access', async () => {
  mockPermission = { granted: false, canAskAgain: false };
  const openSettings = jest.spyOn(Linking, 'openSettings').mockResolvedValue();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Scan />); });
  await act(async () => { await control(tree, 'Open camera settings').props.onPress(); });
  expect(openSettings).toHaveBeenCalledTimes(1);
  expect(mockRequest).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('leaves manual entry available if requesting camera permission fails', async () => {
  mockPermission = { granted: false, canAskAgain: true }; mockRequest.mockRejectedValue(new Error('unavailable'));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Scan />); });
  await act(async () => { await control(tree, 'Enable camera').props.onPress(); });
  expect(mockNotify).toHaveBeenCalledTimes(1);
  expect(control(tree, 'Account or phone number')).toBeTruthy();
  act(() => tree.unmount());
});

it('pauses invalid repeated QR frames and permits an explicit retry', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Scan />); });
  const scan = tree.root.findByProps({ testID: 'camera' }).props.onBarcodeScanned;
  act(() => { scan({ data: 'invalid' }); scan({ data: 'invalid' }); });
  expect(mockNotify).toHaveBeenCalledTimes(1);
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => control(tree, 'Scan again').props.onPress());
  act(() => { scan({ data: '08012345678' }); scan({ data: '08012345678' }); });
  expect(mockReplace).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith({ pathname: '/sendmoney', params: { identifier: '08012345678', mode: 'zitch' } });
  act(() => tree.unmount());
});

it.each(['+2348012345678', '2348012345678'])('keeps an international phone %s intact and navigates only once', async (value) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Scan />); });
  act(() => control(tree, 'Account or phone number').props.onChangeText(value));
  expect(control(tree, 'Continue').props.disabled).toBe(false);
  act(() => { const submit = control(tree, 'Continue').props.onPress; submit(); submit(); });
  expect(mockReplace).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith({ pathname: '/sendmoney', params: { identifier: '08012345678', mode: 'zitch' } });
  act(() => tree.unmount());
});

it('does not enable Continue for an invalid phone', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Scan />); });
  act(() => control(tree, 'Account or phone number').props.onChangeText('12345678901'));
  expect(control(tree, 'Continue').props.disabled).toBe(true);
  act(() => control(tree, 'Continue').props.onPress());
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('routes unavailable business payment links to the verified funding-account flow', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BizPayment />); });
  act(() => control(tree, 'View funding account').props.onPress());
  expect(mockPush).toHaveBeenCalledWith('/addmoney');
  act(() => tree.unmount());
});
