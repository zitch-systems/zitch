import React from 'react';
import renderer, { act } from 'react-test-renderer';
import Upgrade from '../upgrade';

// Native scroll scheduling is outside this form test. Keep the actual option
// filtering and selection while rendering the scroll container synchronously.
jest.mock('react-native/Libraries/Components/ScrollView/ScrollView', () => {
  const ReactActual = jest.requireActual('react');
  const { View } = jest.requireActual('react-native');
  return ({ children, ...props }: any) => ReactActual.createElement(View, props, children);
});

let mockGeneration = 1;
const mockStatus = jest.fn();
const mockRefreshUpgrade = jest.fn();
const mockUpgrade = jest.fn();
const mockAddress = jest.fn();
const mockAddressStatus = jest.fn();
const mockUploadDocument = jest.fn();
const mockDocuments = jest.fn();
const mockLiveness = jest.fn();
const mockOpenBrowser = jest.fn();
jest.mock('expo-web-browser', () => ({ openBrowserAsync: (...args: any[]) => mockOpenBrowser(...args) }));
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
  status: () => mockStatus(), refreshUpgrade: () => mockRefreshUpgrade(), upgrade: (...args: any[]) => mockUpgrade(...args),
  address: (...args: any[]) => mockAddress(...args), addressStatus: (...args: any[]) => mockAddressStatus(...args),
  uploadDocument: (...args: any[]) => mockUploadDocument(...args), documents: () => mockDocuments(),
  livenessStart: () => mockLiveness(),
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
    Btn: ({ label, style: _style, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)),
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
  mockRefreshUpgrade.mockReset().mockResolvedValue(tier1);
  mockCameraPermission.mockResolvedValue({ granted: true });
  mockCamera.mockResolvedValue({ canceled: false, assets: [{ base64: 'face-image' }] });
  mockPick.mockResolvedValue({ canceled: false, assets: [{ uri: 'local-document', size: 100 }] }); mockRead.mockResolvedValue('encoded-document');
  mockUploadDocument.mockReset().mockResolvedValue({ success: true }); mockDocuments.mockReset().mockResolvedValue({ success: true, documents: [] });
  mockLiveness.mockReset().mockResolvedValue({ success: true, url: 'https://api.example/verify/face/token' });
  mockOpenBrowser.mockReset().mockImplementation(async () => { mockDocuments.mockResolvedValue({ success: true, documents: ['idCardFront', 'utilityBill', 'customerSignature', 'userPhoto'] }); });
});
afterEach(async () => { if (tree) await act(async () => tree.unmount()); });

it('resumes a pending upgrade without requesting another set of documents', async () => {
  mockStatus.mockResolvedValue({ ...tier1, upgrade_status: { reference: 'ZU-existing', tier: 2, state: 'submitted', pending: true } });
  await mount();
  expect(output()).toContain('Your upgrade is being processed.');
  expect(control('Check upgrade status')).toBeTruthy();
  expect(output()).not.toContain('Upload NIN slip');
  expect(mockUpgrade).not.toHaveBeenCalled();
});

it('checks the existing bank upgrade and shows approval without submitting again', async () => {
  mockStatus.mockResolvedValue({ ...tier1, upgrade_status: { reference: 'ZU-existing', tier: 2, state: 'submitted', pending: true } });
  mockRefreshUpgrade.mockResolvedValue({ ...tier1, bank_tier: 2, tier: 2,
    upgrade_status: { reference: 'ZU-existing', tier: 2, state: 'approved', pending: false } });
  await mount();
  await act(async () => { await control('Check upgrade status').props.onPress(); });
  expect(mockRefreshUpgrade).toHaveBeenCalledTimes(1);
  expect(mockUpgrade).not.toHaveBeenCalled();
  expect(output()).toContain('Upgrade approved.');
  expect(output()).toContain('Upgrade to Tier 2');
  expect(output()).not.toContain('Verify your home address');
});

it('preserves a pending upgrade during a status outage', async () => {
  const pending = { ...tier1, upgrade_status: { reference: 'ZU-existing', tier: 3, state: 'review', pending: true } };
  mockStatus.mockResolvedValue(pending);
  mockRefreshUpgrade.mockResolvedValue({ ...pending, upgrade_refresh_unavailable: true });
  await mount();
  await act(async () => { await control('Check upgrade status').props.onPress(); });
  expect(output()).toContain('Could not refresh your upgrade status.');
  expect(control('Check upgrade status')).toBeTruthy();
  expect(mockUpgrade).not.toHaveBeenCalled();
});

it('goes straight to the next tier with no tier tabs', async () => {
  await mount();
  expect(output()).toContain('Upgrade to Tier 2');
  expect(output()).not.toContain('Tier 3 address'); expect(output()).not.toContain('Submit for bank review');
});

it('asks only for the NIN and its slip photo, never an ID type, number, dates, back side or BVN', async () => {
  mockStatus.mockResolvedValue({ ...tier1, bvn_on_file: true }); await mount();
  expect(field('NIN')).toBeTruthy(); expect(output()).toContain('Upload NIN slip');
  for (const gone of ['BVN"', 'Issue date', 'Expiry date', 'ID back', 'ID number', 'Driver', 'Passport photo']) expect(output()).not.toContain(gone);
});

it('asks for the BVN once when the account was opened before it was kept', async () => {
  await mount();
  expect(field('BVN')).toBeTruthy();
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

it('uploads each file as it is picked, opens the face-check link, and sends the upgrade', async () => {
  await mount();
  await type('BVN', '12345678901'); await type('NIN', '10987654321');
  await type('House number', '12'); await type('Street', 'Test Road'); await type('City or town', 'Ikeja');
  await type('State', 'Lagos'); await type('Local government area', 'Ikeja'); await type('Nearest landmark', 'School');
  for (const doc of ['Upload NIN slip', 'Upload utility bill', 'Upload signature']) await act(async () => { await control(doc).props.onPress(); });
  expect(mockPick).toHaveBeenCalledWith(expect.objectContaining({ type: ['image/*', 'application/pdf'] }));
  expect(mockUploadDocument).toHaveBeenCalledWith('idCardFront', 'encoded-document');
  await act(async () => { await control('Verify your face').props.onPress(); });
  expect(mockOpenBrowser).toHaveBeenCalledWith('https://api.example/verify/face/token');
  expect(output()).toContain('Face verified');
  await act(async () => { await control('Upgrade to Tier 2').props.onPress(); });
  const [tier, body] = mockUpgrade.mock.calls[0];
  expect(tier).toBe(2);
  expect(body).toEqual(expect.objectContaining({ nin: '10987654321', houseNumber: '12', state: 'Lagos', accountName: 'Ada Eze' }));
  for (const gone of ['idType', 'idNumber', 'idCardFront', 'userPhoto']) expect(body).not.toHaveProperty(gone);
  expect(output()).toContain('Your upgrade is being reviewed.');
});

it('marks every missing field in red instead of sending an incomplete upgrade', async () => {
  await mount();
  await act(async () => { await control('Upgrade to Tier 2').props.onPress(); });
  expect(mockUpgrade).not.toHaveBeenCalled();
  expect(output()).toContain('This field is required');
  expect(field('NIN').props.error).toBe('This field is required');
  expect(field('House number').props.error).toBe('This field is required');
});

it('refuses files over 5 MB before uploading', async () => {
  mockPick.mockResolvedValue({ canceled: false, assets: [{ uri: 'big', size: 6 * 1024 * 1024 }] }); await mount();
  await act(async () => { await control('Upload NIN slip').props.onPress(); });
  expect(mockUploadDocument).not.toHaveBeenCalled();
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

