import React from 'react';
import renderer, { act } from 'react-test-renderer';
import PaymentScreen from '../PaymentScreen';

let mockGeneration = 1;
let mockPin: any;
let mockConfirmation: any;
const mockReload = jest.fn(async () => {});
const mockWallet = { balance: 500, phoneNumber: '+2347041267880', billPaymentsAvailable: true, reload: mockReload };
const mockCatalog = jest.fn();
const mockValidate = jest.fn();
const mockPost = jest.fn();
const mockAcquire = jest.fn();
const mockClear = jest.fn();
jest.mock('@/components/AuthGuard', () => ({ children }: any) => children);
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('@/lib/api', () => ({ apiPost: (...args: any[]) => mockPost(...args) }));
jest.mock('@/lib/services/banking', () => ({ banking: { catalog: (...args: any[]) => mockCatalog(...args), validate: (...args: any[]) => mockValidate(...args) } }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: (...args: any[]) => mockAcquire(...args), clearSpendAttempt: (...args: any[]) => mockClear(...args) }));
jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111' } }), font: { regular: 'regular' } }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual('react');
  const { View, Pressable, Text, TextInput } = jest.requireActual('react-native');
  return {
    Screen: ({ children }: any) => ReactActual.createElement(View, null, children), Header: () => null,
    Field: ({ label, ...props }: any) => ReactActual.createElement(TextInput, { accessibilityLabel: label, ...props }),
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)),
    PinSheet: (props: any) => { mockPin = props; return null; }, money: (n: number) => `NGN ${n}`,
  };
});
jest.mock('@/components/design/flowkit', () => ({ ConfirmSheet: (props: any) => { mockConfirmation = props; return null; } }));

let tree: renderer.ReactTestRenderer;
const control = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
async function mount(kind: 'airtime' | 'data' | 'bill' = 'airtime') {
  await act(async () => { tree = renderer.create(<PaymentScreen kind={kind} />); });
}
async function review() {
  await act(async () => { control('Amount (NGN)').props.onChangeText('100'); });
  await act(async () => { control('Review payment').props.onPress(); });
  await act(async () => { mockConfirmation.onPay(); });
}
beforeEach(() => {
  jest.clearAllMocks(); mockGeneration = 1; mockWallet.billPaymentsAvailable = true;
  mockCatalog.mockResolvedValue({ success: true, data: [] });
  mockAcquire.mockResolvedValue('stable-payment-key');
  mockClear.mockResolvedValue(undefined);
  mockPost.mockResolvedValue({ status: 202, json: async () => ({ success: false, pending: true, reference: 'BANK-REF' }) });
});
afterEach(async () => { if (tree) await act(async () => { tree.unmount(); }); });

it('normalizes the verified Nigerian phone and requires a valid amount', async () => {
  await mount();
  expect(control('Phone number').props.value).toBe('07041267880');
  expect(control('Review payment').props.disabled).toBe(true);
  await act(async () => { control('Amount (NGN)').props.onChangeText('501'); });
  expect(control('Review payment').props.disabled).toBe(true);
});

it('submits once on rapid PIN completion and retains the key for pending money', async () => {
  await mount(); await review();
  const complete = mockPin.onComplete;
  await act(async () => { complete('582940'); complete('582940'); });
  expect(mockPost).toHaveBeenCalledTimes(1);
  expect(mockPost).toHaveBeenCalledWith('/api/banking/purchase/', expect.objectContaining({ idempotency_key: 'stable-payment-key', phone: '07041267880', amount: '100' }));
  expect(mockClear).not.toHaveBeenCalled();
  expect(control('View transaction history')).toBeTruthy();
});

it('keeps the same attempt after a connection interruption', async () => {
  mockPost.mockRejectedValue(new Error('connection lost'));
  await mount(); await review();
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockClear).not.toHaveBeenCalled();
  expect(control('View transaction history')).toBeTruthy();
});

it('does not pay after capabilities are revoked while the PIN sheet is open', async () => {
  await mount(); await review();
  mockWallet.billPaymentsAvailable = false;
  await act(async () => { tree.update(<PaymentScreen kind="airtime" />); });
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockPost).not.toHaveBeenCalled();
});

it('does not send an old account request after session rotation', async () => {
  let release!: (key: string) => void;
  mockAcquire.mockReturnValue(new Promise<string>((resolve) => { release = resolve; }));
  await mount(); await review();
  await act(async () => { mockPin.onComplete('582940'); });
  mockGeneration += 1;
  await act(async () => { release('old-account-key'); });
  expect(mockPost).not.toHaveBeenCalled();
});

it('ignores data plans returned for a phone that was changed during lookup', async () => {
  let release!: (value: unknown) => void;
  mockCatalog.mockReturnValue(new Promise((resolve) => { release = resolve; }));
  await mount('data');
  await act(async () => { control('Load data plans for this phone').props.onPress(); });
  await act(async () => { control('Phone number').props.onChangeText('08011112222'); });
  await act(async () => { release({ success: true, data: [{ productId: 'OLD', amount: '100', dataBundle: 'Stale plan' }] }); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Stale plan');
  expect(control('Review payment').props.disabled).toBe(true);
});

it('shows a retryable catalog error without leaving the screen busy', async () => {
  mockCatalog.mockRejectedValue(new Error('unavailable'));
  await mount('bill');
  expect(control('Retry loading bill categories').props.disabled).toBe(false);
  expect(mockPost).not.toHaveBeenCalled();
});

async function chooseBill() {
  mockCatalog.mockImplementation(async (kind: string) => ({ success: true, data:
    kind === 'categories' ? [{ id: 'C1', name: 'Electricity' }] :
    kind === 'billers' ? [{ id: 'B1', name: 'Current Biller' }] :
    [{ fieldName: 'customerId', validation: 'Y' }, { fieldName: 'amount', validation: 'Y' },
      { fieldName: 'itemId', isSelectData: 'Y', validation: 'Y', items: [{ itemId: 'I1', itemName: 'Fixed product', amount: '100' }] }] }));
  await mount('bill');
  await act(async () => { control('Electricity').props.onPress(); });
  await act(async () => { control('Current Biller').props.onPress(); });
  await act(async () => { control('Fixed product · NGN 100').props.onPress(); });
  await act(async () => { control('Customer, meter or subscription number').props.onChangeText('customer-1'); });
}
async function verifyBill(reference = 'validation-1') {
  mockValidate.mockResolvedValue({ success: true, validation_reference: reference, customer_name: 'Ada Eze', amount: '100' });
  await act(async () => { control('Verify customer').props.onPress(); });
}
async function payBill() {
  await act(async () => { control('Review payment').props.onPress(); });
  await act(async () => { mockConfirmation.onPay(); });
  await act(async () => { mockPin.onComplete('582940'); });
}

it('never treats a successful HTTP response with failed customer validation as verified', async () => {
  await chooseBill(); mockValidate.mockResolvedValue({ success: false, customer_name: 'Untrusted name', validation_reference: 'untrusted' });
  await act(async () => { control('Verify customer').props.onPress(); });
  expect(control('Review payment').props.disabled).toBe(true);
  expect(JSON.stringify(tree.toJSON())).not.toContain('Untrusted name');
});

it('binds fixed bill prices and validation to the current customer', async () => {
  await chooseBill(); await verifyBill();
  expect(control('Amount (NGN)').props.value).toBe('100');
  expect(control('Amount (NGN)').props.editable).toBe(false);
  expect(control('Review payment').props.disabled).toBe(false);
  await act(async () => { control('Customer, meter or subscription number').props.onChangeText('customer-2'); });
  expect(control('Review payment').props.disabled).toBe(true);
  expect(JSON.stringify(tree.toJSON())).not.toContain('Ada Eze');
});

async function chooseNoPlanBill(fields: object[] = []) {
  mockCatalog.mockImplementation(async (kind: string) => ({ success: true, data:
    kind === 'categories' ? [{ id: 'C1', name: 'Subscriptions' }] :
    kind === 'billers' ? [{ id: 'B1', name: 'No-plan biller' }] :
    [{ fieldName: 'customerId', validation: 'Y' }, { fieldName: 'amount', validation: 'Y' }, ...fields] }));
  await mount('bill');
  await act(async () => { control('Subscriptions').props.onPress(); });
  await act(async () => { control('No-plan biller').props.onPress(); });
  await act(async () => { control('Customer, meter or subscription number').props.onChangeText('customer-1'); });
  await act(async () => { control('Amount (NGN)').props.onChangeText('100'); });
}

it('validates and purchases a no-plan bill without inventing an item', async () => {
  await chooseNoPlanBill();
  expect(control('Verify customer').props.disabled).toBe(false);
  await verifyBill(); await payBill();
  expect(mockValidate).toHaveBeenCalledWith({ biller_id: 'B1', item_id: '', customer: 'customer-1', amount: '100', inputs: {} });
  expect(mockPost).toHaveBeenCalledTimes(1);
});

it('collects required customer names and invalidates approval when they change', async () => {
  await chooseNoPlanBill([
    { fieldName: 'firstname', validation: 'Y', fieldDescription: 'First name on account' },
    { fieldName: 'lastname', validation: 'Y' },
  ]);
  expect(control('Verify customer').props.disabled).toBe(true);
  await act(async () => { control('Customer first name').props.onChangeText('Ada'); });
  await act(async () => { control('Customer last name').props.onChangeText('Eze'); });
  expect(control('Verify customer').props.disabled).toBe(false);
  await verifyBill();
  expect(mockValidate).toHaveBeenCalledWith(expect.objectContaining({ inputs: { firstname: 'Ada', lastname: 'Eze' } }));
  await act(async () => { control('Customer last name').props.onChangeText('Changed'); });
  expect(control('Review payment').props.disabled).toBe(true);
});

it('does not request names excluded from bank validation', async () => {
  await chooseNoPlanBill([{ fieldName: 'firstname', validation: 'N' }]);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Customer first name' })).toHaveLength(0);
  expect(control('Verify customer').props.disabled).toBe(false);
});

it('requires a vend option but lets the customer enter an unpriced electricity amount', async () => {
  await chooseNoPlanBill([{ fieldName: 'itemId', validation: 'Y', isSelectData: 'Y',
    items: [{ itemId: 'VT01', itemName: 'PREPAID' }, { itemId: 'VT02', itemName: 'POSTPAID' }] }]);
  expect(control('Verify customer').props.disabled).toBe(true);
  await act(async () => { control('PREPAID').props.onPress(); });
  expect(control('Amount (NGN)').props.editable).toBe(true);
  await act(async () => { control('Amount (NGN)').props.onChangeText('100'); });
  await verifyBill();
  expect(mockValidate).toHaveBeenCalledWith(expect.objectContaining({ item_id: 'VT01', amount: '100' }));
});

it('does not accept an approval for a different bill amount', async () => {
  await chooseNoPlanBill();
  mockValidate.mockResolvedValue({ success: true, validation_reference: 'wrong-price', customer_name: 'Ada', amount: '101' });
  await act(async () => { control('Verify customer').props.onPress(); });
  expect(control('Review payment').props.disabled).toBe(true);
  expect(mockPost).not.toHaveBeenCalled();
});

it('submits customer validation once on rapid taps and clears a rejected earlier approval', async () => {
  await chooseNoPlanBill(); await verifyBill();
  let finish!: (value: unknown) => void;
  mockValidate.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const validate = control('Verify customer').props.onPress;
  await act(async () => { validate(); validate(); });
  expect(mockValidate).toHaveBeenCalledTimes(2); // Initial approval + one renewal.
  await act(async () => { finish({ success: false, message: 'Validation unavailable' }); });
  expect(control('Review payment').props.disabled).toBe(true);
});

it('directs a confirmed payment with a delayed token to its recoverable receipt', async () => {
  mockPost.mockResolvedValue({ status: 200, json: async () => ({ success: true, receipt_pending: true, reference: 'TOKEN-PENDING' }) });
  await chooseNoPlanBill(); await verifyBill(); await payBill();
  expect(JSON.stringify(tree.toJSON())).toContain('Your token is being retrieved');
  expect(control('View transaction history')).toBeTruthy();
  expect(mockClear).toHaveBeenCalledTimes(1);
});

it('ignores a bill validation response after the customer changes', async () => {
  let finish!: (value: unknown) => void;
  await chooseBill(); mockValidate.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await act(async () => { control('Verify customer').props.onPress(); });
  await act(async () => { control('Customer, meter or subscription number').props.onChangeText('customer-2'); });
  await act(async () => { finish({ success: true, validation_reference: 'old-customer', customer_name: 'Wrong customer' }); });
  expect(control('Review payment').props.disabled).toBe(true);
  expect(JSON.stringify(tree.toJSON())).not.toContain('Wrong customer');
});

it('retains the same durable payment intent when bill validation is renewed', async () => {
  await chooseBill(); await verifyBill('first-validation'); await payBill();
  const first = mockAcquire.mock.calls[0];
  await act(async () => { tree.unmount(); });
  await chooseBill(); await verifyBill('renewed-validation'); await payBill();
  expect(mockAcquire.mock.calls[1]).toEqual(first);
  expect(mockAcquire.mock.calls[1][1]).not.toContain('validation');
});

it('starts a different durable intent when the bill amount changes', async () => {
  await mount(); await review();
  await act(async () => { mockPin.onComplete('582940'); });
  const first = mockAcquire.mock.calls[0][1];
  await act(async () => { tree.unmount(); }); await mount();
  await act(async () => { control('Amount (NGN)').props.onChangeText('200'); });
  await act(async () => { control('Review payment').props.onPress(); });
  await act(async () => { mockConfirmation.onPay(); });
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockAcquire.mock.calls[1][1]).not.toBe(first);
});

it('prevents an unkeyed payment when durable storage is unavailable', async () => {
  mockAcquire.mockRejectedValue(new Error('disk error')); await mount(); await review();
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockPost).not.toHaveBeenCalled();
  expect(mockPin.error).toContain('Could not safely save');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Payment completed');
});

it.each(['resolve', 'reject'])('ignores a late payment %s after the customer session changes', async (completion) => {
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise((resolve, reject) => { finish = completion === 'resolve' ? resolve : reject; }));
  await mount(); await review();
  await act(async () => { mockPin.onComplete('582940'); });
  mockGeneration += 1;
  await act(async () => { finish(completion === 'resolve' ? { status: 200, json: async () => ({ success: true }) } : new Error('interrupted')); });
  expect(mockClear).not.toHaveBeenCalled();
  expect(JSON.stringify(tree.toJSON())).not.toContain('Payment completed');
});

it('requires review before a PIN callback can submit payment', async () => {
  await mount(); await act(async () => { control('Amount (NGN)').props.onChangeText('100'); });
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockPost).not.toHaveBeenCalled(); expect(mockAcquire).not.toHaveBeenCalled();
});

it('shows a pending duplicate as processing and keeps its attempt key', async () => {
  mockPost.mockResolvedValue({ status: 202, json: async () => ({ success: false, pending: true, duplicate: true, reference: 'ORIGINAL' }) });
  await mount(); await review(); await act(async () => { mockPin.onComplete('582940'); });
  expect(mockClear).not.toHaveBeenCalled();
  expect(JSON.stringify(tree.toJSON())).toContain('Payment is processing');
  expect(JSON.stringify(tree.toJSON())).toContain('ORIGINAL');
});

it('keeps an ambiguous 2xx response pending rather than clearing its retry key', async () => {
  mockPost.mockResolvedValue({ status: 200, json: async () => ({}) });
  await mount(); await review(); await act(async () => { mockPin.onComplete('582940'); });
  expect(mockClear).not.toHaveBeenCalled(); expect(control('View transaction history')).toBeTruthy();
});

it('uses the catalog data-plan price and forbids editing it', async () => {
  mockCatalog.mockResolvedValue({ success: true, data: [{ productId: 'P1', dataBundle: '1 GB', validity: '30 days', amount: '100' }] });
  await mount('data'); await act(async () => { control('Load data plans for this phone').props.onPress(); });
  await act(async () => { control('1 GB · 30 days · NGN 100').props.onPress(); });
  expect(control('Amount (NGN)').props.value).toBe('100');
  expect(control('Amount (NGN)').props.editable).toBe(false);
  await act(async () => { control('Review payment').props.onPress(); });
  await act(async () => { mockConfirmation.onPay(); });
  await act(async () => { mockPin.onComplete('582940'); });
  expect(mockPost).toHaveBeenCalledWith('/api/banking/purchase/', expect.objectContaining({ product_id: 'P1', amount: '100' }));
});

it('does not repaint a receipt from another account during marker cleanup', async () => {
  let finish!: () => void;
  mockPost.mockResolvedValue({ status: 200, json: async () => ({ success: true, reference: 'OLD-RECEIPT' }) });
  mockClear.mockReturnValue(new Promise<void>((resolve) => { finish = resolve; }));
  await mount(); await review(); await act(async () => { mockPin.onComplete('582940'); });
  mockGeneration += 1; await act(async () => { finish(); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('OLD-RECEIPT');
  expect(mockReload).not.toHaveBeenCalled();
});
