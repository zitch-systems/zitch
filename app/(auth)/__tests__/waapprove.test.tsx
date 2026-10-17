import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import WaApprove from '@/app/(auth)/waapprove';

const mockApiJson = jest.fn();
const mockIcon = jest.fn();
const mockClearPendingApproval = jest.fn(async (_token?: string, _generation?: number) => {});
const mockParams = { token: 'ap1.signed' };
let mockSessionGeneration = 0;
const trees: renderer.ReactTestRenderer[] = [];

jest.mock('expo-router', () => ({
  router: { replace: jest.fn(), back: jest.fn() },
  useLocalSearchParams: () => mockParams,
}));
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockSessionGeneration }));
jest.mock('@/lib/pendingApproval', () => ({
  rememberWhatsAppApproval: jest.fn(async () => {}),
  clearPendingWhatsAppApproval: (...args: [string?, number?]) => mockClearPendingApproval(...args),
}));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/AuthGuard', () => ({ __esModule: true, default: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => (props: { name: string }) => { mockIcon(props.name); return null; });
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#090', ink1: '#111', ink3: '#333', surface: '#fff', line: '#eee', lime: '#0c0', amber: '#fa0', red: '#f00' } }),
  font: { bold: 'bold', regular: 'regular', semibold: 'semibold', extrabold: 'extrabold' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, Text: RText, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null,
    Btn: (props: any) => R.createElement(Pressable, { ...props, accessibilityLabel: props.label }, R.createElement(RText, null, props.label)),
    PinPad: (props: any) => R.createElement(Pressable, { accessibilityLabel: 'pin', onPress: () => props.onComplete('1234') }),
  };
});

const texts = (tree: renderer.ReactTestRenderer) =>
  tree.root.findAllByType(Text).map((node) => [].concat(node.props.children).join(''));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

async function renderApproval() {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<WaApprove />); });
  trees.push(tree);
  return tree;
}

async function switchToken(tree: renderer.ReactTestRenderer, token: string) {
  mockParams.token = token;
  await act(async () => { tree.update(<WaApprove />); });
}

const executions = () => mockApiJson.mock.calls.filter(([path]) => path.endsWith('/execute/'));

async function approveWith(execute: Record<string, unknown>, preview: Record<string, unknown> = {}) {
  mockApiJson.mockImplementation(async (path: string) => (
    path.endsWith('/preview/')
      ? { success: true, summary: 'Send ₦5,000.00 to JOHN DOE', action_type: 'transfer', ...preview }
      : execute
  ));
  const tree = await renderApproval();
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  return tree;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiJson.mockReset();
  mockParams.token = 'ap1.signed';
  mockSessionGeneration = 0;
});

afterEach(() => { act(() => { trees.splice(0).forEach((tree) => tree.unmount()); }); });

it('celebrates only a payment that actually went through', async () => {
  const tree = await approveWith({ success: true, outcome: 'success', message: '₦5,000.00 sent to JOHN DOE' });
  expect(texts(tree)).toContain('Payment approved');
  expect(mockIcon).toHaveBeenCalledWith('check');
  expect(mockClearPendingApproval).toHaveBeenCalledWith('ap1.signed', 0);
});

it('does not show a refused payment as approved', async () => {
  const tree = await approveWith({ success: true, outcome: 'failed', message: '❌ Your transfer failed: declined.' });
  expect(texts(tree)).toContain('Payment not completed');
  expect(texts(tree)).not.toContain('Payment approved');
  expect(texts(tree)).toContain('❌ Your transfer failed: declined.');
});

it('says a still-settling payment is processing', async () => {
  const tree = await approveWith({ success: true, outcome: 'pending', message: '⏳ Still processing.' });
  expect(texts(tree)).toContain('Payment processing');
  expect(texts(tree)).not.toContain('Payment approved');
});

it('keeps the approved heading for a server that sends no outcome', async () => {
  const tree = await approveWith({ success: true, message: 'Done ✅' });
  expect(texts(tree)).toContain('Payment approved');
});

it('names an identity confirmation as one rather than a payment', async () => {
  const tree = await approveWith(
    { success: true, outcome: 'done', message: 'Unlocked ✅ - see the chat.' },
    { action_type: 'unlock', summary: "It's been a while - confirm it's you to continue" },
  );
  expect(texts(tree)).toContain('Identity confirmed');
  expect(texts(tree)).not.toContain('Payment approved');
});

it('removes the previous summary and PIN until the new token has been previewed', async () => {
  const nextPreview = deferred<Record<string, unknown>>();
  mockApiJson.mockImplementation(async (path: string, body: { token: string }) => {
    if (path.endsWith('/execute/')) return { success: true, outcome: 'pending' };
    return body.token === 'ap1.signed'
      ? { success: true, summary: 'First payment' }
      : nextPreview.promise;
  });
  const tree = await renderApproval();
  const oldPinCallback = tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress;
  await switchToken(tree, 'ap2.signed');
  expect(texts(tree)).not.toContain('First payment');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'pin' })).toHaveLength(0);
  await act(async () => { await oldPinCallback(); });
  expect(executions()).toHaveLength(0);
  await act(async () => { nextPreview.resolve({ success: true, summary: 'Second payment' }); });
  expect(texts(tree)).toContain('Second payment');
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  expect(executions()).toEqual([['/api/whatsapp/approve/execute/', { token: 'ap2.signed', transaction_pin: '1234' }]]);
});

it('ignores a late preview from an earlier link', async () => {
  const first = deferred<Record<string, unknown>>();
  const second = deferred<Record<string, unknown>>();
  mockApiJson.mockImplementation((_path: string, body: { token: string }) => body.token === 'ap1.signed' ? first.promise : second.promise);
  const tree = await renderApproval();
  await switchToken(tree, 'ap2.signed');
  await act(async () => { first.resolve({ success: false, message: 'First link expired' }); });
  expect(texts(tree)).not.toContain('First link expired');
  expect(mockClearPendingApproval).not.toHaveBeenCalled();
  expect(tree.root.findAllByProps({ accessibilityLabel: 'pin' })).toHaveLength(0);
  await act(async () => { second.resolve({ success: true, summary: 'Second payment' }); });
  expect(texts(tree)).toContain('Second payment');
});

it('does not let an old execution overwrite or clear a newer pending link', async () => {
  const execute = deferred<Record<string, unknown>>();
  mockApiJson.mockImplementation((path: string, body: { token: string }) => path.endsWith('/execute/')
    ? execute.promise : Promise.resolve({ success: true, summary: body.token === 'ap1.signed' ? 'First payment' : 'Second payment' }));
  const tree = await renderApproval();
  let execution!: Promise<void>;
  await act(async () => { execution = tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  await switchToken(tree, 'ap2.signed');
  await act(async () => { execute.resolve({ success: true, outcome: 'success', message: 'First payment sent' }); await execution; });
  expect(texts(tree)).toContain('Second payment');
  expect(texts(tree)).not.toContain('First payment sent');
  expect(texts(tree)).not.toContain('Payment approved');
  expect(mockClearPendingApproval).not.toHaveBeenCalled();
});

it('does not execute an old preview after the signed-in session changes', async () => {
  mockApiJson.mockResolvedValue({ success: true, summary: 'First payment' });
  const tree = await renderApproval();
  mockSessionGeneration += 1;
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  expect(executions()).toHaveLength(0);
  expect(texts(tree)).toContain('Your session changed. Open this link again after signing in.');
  expect(mockClearPendingApproval).not.toHaveBeenCalled();
});

it('discards a preview arriving after the signed-in session changes', async () => {
  const preview = deferred<Record<string, unknown>>();
  mockApiJson.mockReturnValue(preview.promise);
  const tree = await renderApproval();
  mockSessionGeneration += 1;
  await act(async () => { preview.resolve({ success: true, summary: 'Previous account payment' }); });
  expect(texts(tree)).not.toContain('Previous account payment');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'pin' })).toHaveLength(0);
  expect(mockClearPendingApproval).not.toHaveBeenCalled();
});

it('discards an execution result after the signed-in session changes', async () => {
  const execute = deferred<Record<string, unknown>>();
  mockApiJson.mockImplementation((path: string) => path.endsWith('/execute/')
    ? execute.promise : Promise.resolve({ success: true, summary: 'First payment' }));
  const tree = await renderApproval();
  let execution!: Promise<void>;
  await act(async () => { execution = tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress(); });
  mockSessionGeneration += 1;
  await act(async () => { execute.resolve({ success: true, outcome: 'success', message: 'First payment sent' }); await execution; });
  expect(texts(tree)).not.toContain('First payment sent');
  expect(texts(tree)).not.toContain('Payment approved');
  expect(mockClearPendingApproval).not.toHaveBeenCalled();
});

it('sends only one approval while the same PIN callback is already running', async () => {
  const execute = deferred<Record<string, unknown>>();
  mockApiJson.mockImplementation((path: string) => path.endsWith('/execute/')
    ? execute.promise : Promise.resolve({ success: true, summary: 'First payment' }));
  const tree = await renderApproval();
  const submit = tree.root.findByProps({ accessibilityLabel: 'pin' }).props.onPress;
  let execution!: Promise<void>;
  await act(async () => { execution = submit(); await submit(); });
  expect(executions()).toHaveLength(1);
  await act(async () => { execute.resolve({ success: true, outcome: 'pending' }); await execution; });
  expect(texts(tree)).toContain('Payment processing');
});

it('does not accept a preview contradicted by an HTTP refusal', async () => {
  mockApiJson.mockResolvedValue({ success: true, _httpOk: false, message: 'Request refused' });
  const tree = await renderApproval();
  expect(tree.root.findAllByProps({ accessibilityLabel: 'pin' })).toHaveLength(0);
  expect(texts(tree)).toContain('Request refused');
});

it.each([undefined, '', '   ', 42, { amount: 5000 }])('requires a meaningful summary before accepting a PIN: %j', async (summary) => {
  mockApiJson.mockResolvedValue({ success: true, summary });
  const tree = await renderApproval();
  expect(tree.root.findAllByProps({ accessibilityLabel: 'pin' })).toHaveLength(0);
  expect(texts(tree)).toContain('We could not verify the details of this request. Open a new approval link from your WhatsApp chat.');
  expect(executions()).toHaveLength(0);
});
