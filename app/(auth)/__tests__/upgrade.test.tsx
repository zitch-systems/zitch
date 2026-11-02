import React from 'react';
import renderer, { act } from 'react-test-renderer';
import Upgrade from '../upgrade';

let mockGeneration = 1;
const mockStatus = jest.fn();
const mockUpgrade = jest.fn();
const mockAddress = jest.fn();
const mockAddressStatus = jest.fn();
const mockCameraPermission = jest.fn();
const mockCamera = jest.fn();
const mockPick = jest.fn();
const mockRead = jest.fn();
jest.mock('@/components/AuthGuard', () => ({ children }: any) => children);
jest.mock('expo-router', () => ({ router: { push: jest.fn(), replace: jest.fn(), back: jest.fn() }, useFocusEffect: (callback: () => (() => void)) => {
  const ReactActual = jest.requireActual('react');
  ReactActual.useEffect(() => callback(), [callback]);
} }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/services/banking', () => ({ banking: {
  status: () => mockStatus(), upgrade: (...args: any[]) => mockUpgrade(...args),
  address: (...args: any[]) => mockAddress(...args), addressStatus: (...args: any[]) => mockAddressStatus(...args),
} }));
jest.mock('expo-image-picker', () => ({ CameraType: { front: 'front' }, requestCameraPermissionsAsync: () => mockCameraPermission(), launchCameraAsync: (...args: any[]) => mockCamera(...args) }));
jest.mock('expo-document-picker', () => ({ getDocumentAsync: (...args: any[]) => mockPick(...args) }));
jest.mock('expo-file-system', () => ({ EncodingType: { Base64: 'base64' }, readAsStringAsync: (...args: any[]) => mockRead(...args) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111', brand: '#099', line: '#ccc', surface: '#fff' } }), font: { regular: 'regular', bold: 'bold' } }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual('react');
  const { View, Pressable, Text, TextInput } = jest.requireActual('react-native');
  return { Screen: ({ children, header }: any) => ReactActual.createElement(View, null, header, children),
    Header: ({ title }: any) => ReactActual.createElement(Text, null, title),
    Card: ({ children }: any) => ReactActual.createElement(View, null, children),
    Field: ({ label, ...props }: any) => ReactActual.createElement(TextInput, { accessibilityLabel: label, ...props }),
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)),
    PillTabs: ({ options, value, onChange }: any) => ReactActual.createElement(View, null, options.map((o: any) => ReactActual.createElement(Pressable,
      { key: o.v, accessibilityLabel: o.label, accessibilityState: { selected: value === o.v }, onPress: () => onChange(o.v) }, ReactActual.createElement(Text, null, o.label)))) };
});

const tier1 = { success: true, bank_tier: 1, account_number: '1100000001', account_name: 'Ada Eze', bvn_verified: true, nin_verified: true,
  address_verified: false, address_verification_available: false, profile: { phoneNo: '08011112222', email: 'ada@example.test' } };
let tree: renderer.ReactTestRenderer;
const output = () => JSON.stringify(tree.toJSON());
const control = (label: string) => tree.root.findAllByProps({ accessibilityLabel: label }).find((n) => n.props.onPress && !n.props.onChangeText)!;
const field = (label: string) => tree.root.findAllByProps({ accessibilityLabel: label }).find((n) => n.props.onChangeText)!;
const type = async (label: string, value: string) => act(async () => { field(label).props.onChangeText(value); });
async function mount() { await act(async () => { tree = renderer.create(<Upgrade />); }); }
beforeEach(() => {
  jest.clearAllMocks(); mockGeneration = 1;
  mockStatus.mockReset().mockResolvedValue(tier1); mockUpgrade.mockReset().mockResolvedValue({ pending: true, message: 'Your upgrade is being reviewed.' });
  mockCameraPermission.mockResolvedValue({ granted: true });
  mockCamera.mockResolvedValue({ canceled: false, assets: [{ base64: 'face-image' }] });
  mockPick.mockResolvedValue({ canceled: false, assets: [{ uri: 'local-document', size: 100 }] }); mockRead.mockResolvedValue('encoded-document');
});
afterEach(async () => { if (tree) await act(async () => tree.unmount()); });

it('goes straight to the next tier with no tier tabs', async () => {
  await mount();
  expect(output()).toContain('Upgrade to Tier 2');
  expect(output()).not.toContain('Tier 3 address'); expect(output()).not.toContain('Submit for bank review');
});

it('asks no dates and no back side for a NIN, and none of the passport photo upload', async () => {
  await mount();
  expect(output()).not.toContain('Issue date'); expect(output()).not.toContain('Expiry date');
  expect(output()).not.toContain('ID back'); expect(output()).not.toContain('Passport photo');
  await act(async () => { control('Passport').props.onPress(); });
  expect(output()).toContain('Issue date'); expect(output()).not.toContain('ID back');
  await act(async () => { control('Driver’s licence').props.onPress(); });
  expect(output()).toContain('Upload ID back');
});

it('uses the number keypad for the house number and suggests states and their LGAs', async () => {
  await mount();
  expect(field('House number').props.keyboardType).toBe('number-pad');
  await act(async () => { field('State').props.onFocus(); });
  await type('State', 'lag');
  await act(async () => { control('Lagos').props.onPress(); });
  expect(field('State').props.value).toBe('Lagos');
  await act(async () => { field('Local government area').props.onFocus(); });
  await type('Local government area', 'ik');
  expect(output()).toContain('Ikeja'); expect(output()).toContain('Ikorodu');
  await type('Local government area', 'My own LGA');
  expect(field('Local government area').props.value).toBe('My own LGA');
});

it('captures the face in the app and sends it with the upgrade, then drops the documents', async () => {
  await mount();
  await type('BVN', '12345678901'); await type('NIN', '10987654321');
  await type('House number', '12'); await type('Street', 'Test Road'); await type('City or town', 'Ikeja');
  await type('State', 'Lagos'); await type('Local government area', 'Ikeja'); await type('Nearest landmark', 'School');
  for (const doc of ['Upload ID front', 'Upload Utility bill', 'Upload Signature']) await act(async () => { await control(doc).props.onPress(); });
  expect(control('Upgrade to Tier 2').props.disabled).toBe(true);
  await act(async () => { await control('Verify your face').props.onPress(); });
  expect(mockCamera).toHaveBeenCalledWith(expect.objectContaining({ cameraType: 'front' }));
  expect(mockPick).toHaveBeenCalledWith(expect.objectContaining({ type: ['application/pdf', 'image/jpeg', 'image/png'] }));
  await act(async () => { await control('Upgrade to Tier 2').props.onPress(); });
  const [tier, body] = mockUpgrade.mock.calls[0];
  expect(tier).toBe(2);
  expect(body).toEqual(expect.objectContaining({ userPhoto: 'face-image', idType: '1', houseNumber: '12', state: 'Lagos', accountName: 'Ada Eze' }));
  expect(body).not.toHaveProperty('idCardBack'); expect(body).not.toHaveProperty('idIssueDate');
  expect(output()).toContain('Your upgrade is being reviewed.'); expect(output()).not.toContain('encoded-document');
});

it('asks Tier 3 customers to verify their address first and says so when it is unavailable', async () => {
  mockStatus.mockResolvedValue({ ...tier1, bank_tier: 2 }); await mount();
  expect(output()).toContain('Verify your home address');
  expect(output()).toContain('Address verification is not available yet');
  expect(output()).not.toContain('BVN and NIN'); expect(mockAddress).not.toHaveBeenCalled();
});

it('shows the Tier 3 form without an address section once the address is verified', async () => {
  mockStatus.mockResolvedValue({ ...tier1, bank_tier: 2, address_verified: true }); await mount();
  expect(output()).toContain('Upgrade to Tier 3'); expect(output()).not.toContain('House number');
});

it('sends customers without both identities back to verify the missing one', async () => {
  mockStatus.mockResolvedValue({ ...tier1, nin_verified: false }); await mount();
  expect(output()).toContain('Verify my NIN');
});
