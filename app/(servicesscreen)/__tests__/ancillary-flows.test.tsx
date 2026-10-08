import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { AppState, Linking, Pressable, Share } from 'react-native';
import { Btn, Field, PinSheet, PillTabs, PickerSheet } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import LinkWhatsApp from '@/app/(servicesscreen)/linkwhatsapp';
import Statement from '@/app/(servicesscreen)/statement';
import Limits from '@/app/(servicesscreen)/limits';
import Invite from '@/app/(servicesscreen)/invite';
import Support from '@/app/(servicesscreen)/support';
import Settings from '@/app/(servicesscreen)/settings';
import { setBiometricEnabled, authenticate } from '@/lib/biometrics';
import { clearSession } from '@/lib/secureStore';

const mockApiJson = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args), apiPost: (...args: unknown[]) => mockApiPost(...args) }));
jest.mock('@/lib/biometrics', () => ({ isBiometricAvailable: jest.fn(async () => true), isBiometricEnabled: jest.fn(async () => false), setBiometricEnabled: jest.fn(async () => {}), authenticate: jest.fn(async () => true) }));
jest.mock('@/lib/secureStore', () => ({ clearSession: jest.fn(async () => {}) }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: jest.fn(), replace: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(async () => {}) }));
jest.mock('expo-linear-gradient', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { LinearGradient: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children) };
});
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@/components/AuthGuard', () => ({ children }: { children: ReactNode }) => children);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff', heroGradient: ['#111', '#222'] }, theme: 'light' }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
  radius: { md: 12, lg: 20 }, iconTint: () => '#eee',
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable, TextInput } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return {
    Screen: Container, Card: Container, NText: Text, Header: () => null, HeaderLink: () => null,
    Sheet: ({ children, open }: { children: ReactNode; open: boolean }) => open ? ReactActual.createElement(View, null, children) : null,
    money: (value: number) => `₦${value}`, Toggle: () => null,
    SelectRow: () => null, PinSheet: () => null, PillTabs: () => null, PickerSheet: () => null,
    Field: (props: object) => ReactActual.createElement(TextInput, props),
    ZItem: ({ title, onPress, right }: { title: string; onPress: () => void; right?: ReactNode }) => ReactActual.createElement(View, null, ReactActual.createElement(Pressable, { accessibilityLabel: title, onPress }, ReactActual.createElement(Text, null, title)), right),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(Pressable, { accessibilityLabel: label, onPress, disabled }, ReactActual.createElement(Text, null, label)),
  };
});

let tree: renderer.ReactTestRenderer | undefined;
const render = async (element: React.ReactElement) => { await act(async () => { tree = renderer.create(element); }); return tree!; };
const button = (label: string) => tree!.root.findAllByType(Btn).find((b) => b.props.label === label)!;
const press = async (label: string) => { await act(async () => { await button(label).props.onPress(); }); };
const output = () => JSON.stringify(tree!.toJSON());
const limitState = { success: true, tier: 1, tier_transaction_limit: '50000', transaction_limit: '50000', daily_transfer_limit: '50000', daily_bill_limit: '35000', self_txn_limit: null };

beforeEach(() => {
  mockApiJson.mockReset();
  mockApiPost.mockReset();
  jest.clearAllMocks();
  AppState.currentState = 'active';
});
afterEach(() => { if (tree) act(() => tree!.unmount()); tree = undefined; jest.useRealTimers(); jest.restoreAllMocks(); });

it('requires a PIN before generating a WhatsApp link and sends the backend transaction_pin contract', async () => {
  mockApiJson.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? { success: true, code: 'A'.repeat(32), wa_link: 'javascript:alert(1)', expires_in: 600 }
    : { success: true, linked: false });
  const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
  await render(<LinkWhatsApp />);
  await press('Generate link code');
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(tree!.root.findByType(PinSheet).props.open).toBe(true);
  await act(async () => { await tree!.root.findByType(PinSheet).props.onComplete('246810'); });
  expect(mockApiJson).toHaveBeenCalledWith('/api/whatsapp/link/start/', { transaction_pin: '246810' }, 15000);
  expect(output()).toContain('A'.repeat(32));
  await press('Open WhatsApp');
  expect(open.mock.calls[0][0]).toMatch(/^https:\/\/wa\.me\/\d+\?text=LINK%20A+$/);
});

it('expires WhatsApp codes using the server TTL and permits regeneration without using the expired code', async () => {
  jest.useFakeTimers();
  mockApiJson.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? { success: true, code: 'A'.repeat(32), expires_in: 30 }
    : { success: true, linked: false });
  await render(<LinkWhatsApp />);
  await press('Generate link code');
  await act(async () => { await tree!.root.findByType(PinSheet).props.onComplete('246810'); });
  await act(async () => { jest.advanceTimersByTime(15000); });
  expect(mockApiJson.mock.calls.filter(([path]) => path.endsWith('/status/'))).toHaveLength(2);
  await act(async () => { jest.advanceTimersByTime(15000); });
  expect(button('Open WhatsApp').props.disabled).toBe(true);
  expect(output()).toContain('This code has expired');
  const count = mockApiJson.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(mockApiJson).toHaveBeenCalledTimes(count);
  await press('Generate a new code');
  expect(tree!.root.findByType(PinSheet).props.open).toBe(true);
});

it('shows a retry after WhatsApp status fails instead of claiming the account is unlinked', async () => {
  mockApiJson.mockResolvedValueOnce({ success: false, message: 'Service unavailable' });
  await render(<LinkWhatsApp />);
  expect(output()).toContain('Service unavailable');
  expect(button('Generate link code')).toBeUndefined();
  mockApiJson.mockResolvedValue({ success: true, linked: true, masked_number: '••••1234' });
  await press('Try again');
  expect(output()).toContain('WhatsApp connected');
});

it('rejects impossible statement dates and accepts a real leap day without rollover', async () => {
  mockApiJson.mockResolvedValue({ success: true, email: 'owner@example.com' });
  await render(<Statement />);
  await act(async () => { tree!.root.findByType(PillTabs).props.onChange('custom'); });
  await act(async () => { tree!.root.findAllByType(Pressable).find((p) => p.props.accessibilityLabel?.startsWith('Start date,'))!.props.onPress(); });
  await act(async () => { tree!.root.findByType(Field).props.onChangeText('2026-02-31'); });
  await press('Set date');
  expect(notify).toHaveBeenCalledWith('Check the date', "That date doesn't exist.");
  await act(async () => { tree!.root.findByType(Field).props.onChangeText('2024-02-29'); });
  await press('Set date');
  expect(output()).toContain('29 Feb, 2024');
});

it('preserves an edited statement email when account lookup finishes late and excludes address from Excel', async () => {
  let resolveProfile!: (value: unknown) => void;
  mockApiJson.mockImplementation((path: string) => path === '/api/kyc/status/'
    ? new Promise((resolve) => { resolveProfile = resolve; }) : Promise.resolve({ success: true }));
  await render(<Statement />);
  await act(async () => { tree!.root.findByProps({ accessibilityLabel: 'Edit email address' }).props.onPress(); });
  await act(async () => { tree!.root.findByType(Field).props.onChangeText('accountant@example.com'); });
  await press('Save');
  await act(async () => { resolveProfile({ success: true, email: 'owner@example.com' }); });
  await act(async () => { tree!.root.findByType(PickerSheet).props.onPick('excel'); });
  await press('Continue');
  expect(mockApiJson).toHaveBeenCalledWith('/api/wallet/statement/request/', expect.objectContaining({ email: 'accountant@example.com', file_type: 'excel', include_address: false }));
});

it.each(['NaN', 'Infinity', '-1', '1.234', '1.2.3', '1,2', '50001'])('rejects invalid transaction limit %s', async (value) => {
  mockApiJson.mockResolvedValue(limitState);
  await render(<Limits />);
  await act(async () => { tree!.root.findByType(Field).props.onChangeText(value); });
  expect(button('Save limit').props.disabled).toBe(true);
});

it('retains a zero freeze and decimal limits without rounding, and sends a canonical PIN-authorized amount', async () => {
  mockApiJson.mockResolvedValue({ ...limitState, self_txn_limit: '0.00', transaction_limit: '0.00' });
  await render(<Limits />);
  expect(tree!.root.findByType(Field).props.value).toBe('0.00');
  expect(button('Remove my limit')).toBeDefined();
  await act(async () => { tree!.root.findByType(Field).props.onChangeText('1,234.56'); });
  expect(button('Save limit').props.disabled).toBe(false);
  await press('Save limit');
  await act(async () => { await tree!.root.findByType(PinSheet).props.onComplete('246810'); });
  expect(mockApiJson).toHaveBeenCalledWith('/api/limits/transaction/', { limit: '1234.56', pin: '246810' });
});

it('disables limit edits after a failed fetch and provides a retry', async () => {
  mockApiJson.mockResolvedValueOnce({ success: false, message: 'No connection' });
  await render(<Limits />);
  expect(button('Save limit').props.disabled).toBe(true);
  expect(tree!.root.findByType(Field).props.editable).toBe(false);
  mockApiJson.mockResolvedValue(limitState);
  await press('Try again');
  expect(tree!.root.findByType(Field).props.editable).toBe(true);
});

it('shares the real website without a fabricated referral code, free-money claim or promised reward', async () => {
  const share = jest.spyOn(Share, 'share').mockResolvedValue({ action: Share.sharedAction });
  await render(<Invite />);
  expect(output()).not.toContain('ZITCH-FRIEND');
  expect(output()).not.toContain('earn rewards when');
  expect(output()).toContain('Invitations are not currently tracked for rewards.');
  await press('Share invite');
  expect(share).toHaveBeenCalledWith({ message: 'Explore Zitch: https://zitch.ng' });
});

it('opens support email directly on Android despite package-visibility canOpenURL false negatives', async () => {
  const canOpen = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(false);
  const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
  await render(<Support />);
  await act(async () => { await tree!.root.findByProps({ accessibilityLabel: 'Email support' }).props.onPress(); });
  expect(canOpen).not.toHaveBeenCalled();
  expect(open).toHaveBeenCalledWith('mailto:support@zitch.ng');
});


it('does not silently enable biometric sign-in when saving the preference fails', async () => {
  (setBiometricEnabled as jest.Mock).mockRejectedValueOnce(new Error('Storage unavailable'));
  await render(<Settings />);
  const toggle = () => tree!.root.findAllByType(Pressable).find((p) => p.props.accessibilityRole === 'switch' && p.props.accessibilityLabel === 'Biometric login')!;
  await act(async () => { toggle().props.onPress(); });
  expect(authenticate).toHaveBeenCalledWith('Enable biometric sign-in');
  expect(notify).toHaveBeenCalledWith('Could not save preference', 'Please try again.');
  expect(toggle().props.accessibilityState.checked).toBe(false);
  expect(toggle().props.disabled).toBe(false);
});

it('clears the local session even when logout cannot reach the server, with duplicate taps suppressed', async () => {
  let rejectLogout!: (error: Error) => void;
  mockApiPost.mockImplementation(() => new Promise((_resolve, reject) => { rejectLogout = reject; }));
  await render(<Settings />);
  const logout = tree!.root.findAllByType(Pressable).find((p) => p.props.accessibilityRole === 'button' && !p.props.accessibilityLabel)!;
  await act(async () => { logout.props.onPress(); logout.props.onPress(); });
  expect(mockApiPost).toHaveBeenCalledTimes(1);
  await act(async () => { rejectLogout(new Error('Network unavailable')); });
  expect(clearSession).toHaveBeenCalledTimes(1);
});
