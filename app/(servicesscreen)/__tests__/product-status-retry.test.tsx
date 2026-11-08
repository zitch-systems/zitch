import React, { type ReactNode } from 'react';
import { ScrollView, Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import MySavings from '@/app/(servicesscreen)/savings';
import FixedSave from '@/app/(servicesscreen)/fixedsave';
import GetLoan from '@/app/(servicesscreen)/getloan';
import { router } from 'expo-router';

const mockListSavings = jest.fn();
const mockGetRates = jest.fn();
const mockGetLoanStatus = jest.fn();
const mockGetToken = jest.fn();
const mockCreateSavings = jest.fn();
const mockRequestLoan = jest.fn();

jest.mock('@/lib/services/savings', () => ({ savingsService: {
  list: (...args: unknown[]) => mockListSavings(...args),
  getRates: (...args: unknown[]) => mockGetRates(...args),
  create: (...args: unknown[]) => mockCreateSavings(...args),
} }));
jest.mock('@/lib/services/loans', () => ({ loansService: {
  getStatus: (...args: unknown[]) => mockGetLoanStatus(...args),
  request: (...args: unknown[]) => mockRequestLoan(...args),
} }));
jest.mock('@/lib/secureStore', () => ({ getToken: (...args: unknown[]) => mockGetToken(...args) }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(async () => 'request-key'), clearSpendAttempt: jest.fn(async () => undefined) }));
jest.mock('@/lib/spendOutcome', () => ({
  classifySpendResponse: (res: { success?: boolean; pending?: boolean }) => res.pending ? 'pending' : res.success ? 'success' : 'failure',
  isRecoveredSpendResponse: (res: { duplicate?: boolean }) => res.duplicate === true,
}));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), replace: jest.fn(), push: jest.fn() },
  useFocusEffect: (callback: () => void) => jest.requireActual<typeof import('react')>('react').useEffect(callback, [callback]),
}));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ balance: 20000, reload: jest.fn() }) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Receipt', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { __esModule: true, default: (props: Record<string, unknown>) => ReactActual.createElement(View, { ...props, testID: 'receipt' }) };
});
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { surface: '#fff', surface3: '#eee', ink1: '#111', ink3: '#333', line: '#ddd', brand: '#099' } }),
  font: { bold: 'bold', regular: 'regular' },
}));
jest.mock('@/components/design/widgets', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Hero: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    SectionLabel: ({ children }: { children: ReactNode }) => ReactActual.createElement(NativeText, null, children),
  };
});
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Label: () => null, QuickAmounts: () => null, BalanceHint: () => null,
    ConfirmSheet: ({ open, ...props }: { open: boolean }) => open ? ReactActual.createElement(View, { ...props, testID: 'confirm' }) : null,
  };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: ({ right }: { right?: ReactNode }) => ReactActual.createElement(View, null, right),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(
      Pressable, { accessibilityLabel: label, onPress, disabled }, ReactActual.createElement(NativeText, null, label),
    ),
    Field: (props: Record<string, unknown>) => ReactActual.createElement(View, { ...props, testID: 'amount-field' }),
    Sheet: ({ open, children }: { open: boolean; children: ReactNode }) => open ? ReactActual.createElement(View, null, children) : null,
    PinPad: (props: Record<string, unknown>) => ReactActual.createElement(View, { ...props, testID: 'pin-pad' }),
    money: (amount: number) => `₦${amount}`, Naira: () => null, NText: NativeText,
  };
});

const textContent = (tree: renderer.ReactTestRenderer) =>
  tree.root.findAllByType(Text).map((node) => String(node.props.children)).join(' ');
const renderPage = async (element: React.ReactElement) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(element); });
  return tree;
};
const offline = { success: false, offline: true, message: 'Service temporarily unavailable.' };
const emptySavings = { success: true, product_available: false, plans: [], total_locked: '0.00', unavailable_message: 'Savings are not available yet.' };
const trees: renderer.ReactTestRenderer[] = [];

beforeEach(() => {
  jest.clearAllMocks();
  [mockListSavings, mockGetRates, mockGetLoanStatus, mockGetToken, mockCreateSavings, mockRequestLoan].forEach((mock) => mock.mockReset());
  mockGetToken.mockResolvedValue('test-token');
});
afterEach(() => {
  act(() => { trees.splice(0).forEach((tree) => tree.unmount()); });
  jest.useRealTimers();
});

const authorize = async (tree: renderer.ReactTestRenderer, label: string) => {
  await act(async () => { tree.root.findByProps({ accessibilityLabel: label }).props.onPress(); });
  await act(async () => {
    tree.root.findByProps({ testID: 'confirm' }).props.onPay();
    jest.advanceTimersByTime(320);
  });
  await act(async () => { await tree.root.findByProps({ testID: 'pin-pad' }).props.onComplete('123456'); });
};

describe('confirmed product receipt terms', () => {
  const rates = { success: true, product_available: true, rates: [{ days: 30, rate: '0.12' }], min: '1000' };
  const loanStatus = { success: true, product_available: true, available: '500000', quote_rate: '0.045', active_loan: null };

  it('retains kobo in the savings estimate and uses the returned plan in the receipt', async () => {
    jest.useFakeTimers();
    mockGetRates.mockResolvedValueOnce(rates);
    mockCreateSavings.mockResolvedValueOnce({ success: true, reference: 'saved-plan', plan: {
      principal: '1000.00', interest: '11.25', maturity_value: '1011.25', rate: '0.135', duration_days: 45,
    } });
    const tree = await renderPage(<FixedSave />); trees.push(tree);
    await act(async () => { tree.root.findByProps({ testID: 'amount-field' }).props.onChangeText('1000'); });
    expect(textContent(tree)).toContain('Estimated interest');
    expect(textContent(tree)).toContain('₦9.86');
    await authorize(tree, 'Continue');
    const receipt = tree.root.findByProps({ testID: 'receipt' }).props;
    expect(receipt.rows).toEqual([
      ['Principal', '₦1000'], ['Rate', '13.5% p.a'], ['Duration', '45 days'], ['Interest at maturity', '₦11.25'], ['Maturity value', '₦1011.25', true],
    ]);
    expect(JSON.stringify(receipt.rows)).not.toContain('Interest earned');
    expect(JSON.stringify(receipt.rows)).not.toContain('9.86');
  });

  it.each([
    { success: true, duplicate: true, reference: 'recovered-save' },
    { success: true, reference: 'invalid-save', plan: { principal: '1000', interest: 'not-a-number', maturity_value: '1010', rate: '0.12', duration_days: 30 } },
  ])('does not fabricate savings terms from estimates when confirmed terms are missing or invalid', async (response) => {
    jest.useFakeTimers();
    mockGetRates.mockResolvedValueOnce(rates);
    mockCreateSavings.mockResolvedValueOnce(response);
    const tree = await renderPage(<FixedSave />); trees.push(tree);
    await act(async () => { tree.root.findByProps({ testID: 'amount-field' }).props.onChangeText('1000'); });
    await authorize(tree, 'Continue');
    const receipt = tree.root.findByProps({ testID: 'receipt' }).props;
    expect(receipt.rows).toEqual([['Confirmed terms', 'View My Fixed Saves']]);
    expect(receipt.message).toContain('confirmed');
    expect(receipt.message).not.toContain('₦');
    receipt.onDone();
    expect(router.replace).toHaveBeenCalledWith('/savings');
  });

  it('retains kobo in the loan estimate and uses the returned loan in the receipt', async () => {
    jest.useFakeTimers();
    mockGetLoanStatus.mockResolvedValueOnce(loanStatus);
    mockRequestLoan.mockResolvedValueOnce({ success: true, reference: 'confirmed-loan', loan: {
      principal: '10002.00', interest: '431.27', total_repayment: '10433.27', tenure_days: 15,
    } });
    const tree = await renderPage(<GetLoan />); trees.push(tree);
    await act(async () => { tree.root.findByProps({ testID: 'amount-field' }).props.onChangeText('10002'); });
    expect(textContent(tree)).toContain('Estimated interest');
    expect(textContent(tree)).toContain('₦450.09');
    await authorize(tree, 'Get ₦10002');
    const receipt = tree.root.findByProps({ testID: 'receipt' }).props;
    expect(receipt.rows).toEqual([
      ['Loan amount', '₦10002'], ['Interest', '₦431.27'], ['Tenure', '15 days'], ['Total repayment', '₦10433.27', true],
    ]);
    expect(JSON.stringify(receipt.rows)).not.toContain('450.09');
  });

  it.each([
    { success: true, duplicate: true, reference: 'recovered-loan' },
    { success: true, reference: 'invalid-loan', loan: { principal: '100000', interest: '4500', total_repayment: '1', tenure_days: 30 } },
  ])('does not fabricate loan obligations when confirmed terms are missing or inconsistent', async (response) => {
    jest.useFakeTimers();
    mockGetLoanStatus.mockResolvedValueOnce(loanStatus);
    mockRequestLoan.mockResolvedValueOnce(response);
    const tree = await renderPage(<GetLoan />); trees.push(tree);
    await authorize(tree, 'Get ₦100000');
    const receipt = tree.root.findByProps({ testID: 'receipt' }).props;
    expect(receipt.rows).toEqual([['Confirmed terms', 'View Loans']]);
    expect(receipt.message).toContain('confirmed');
    expect(receipt.message).not.toContain('₦');
    receipt.onDone();
    expect(router.replace).toHaveBeenCalledWith('/loan');
  });
});

describe('product status failures and retry', () => {
  it('does not invent an empty savings balance after a failed first lookup and can retry', async () => {
    mockListSavings.mockResolvedValueOnce(offline).mockResolvedValueOnce(emptySavings);
    const tree = await renderPage(<MySavings />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to load your savings');
    expect(textContent(tree)).not.toContain('Total locked');
    expect(textContent(tree)).not.toContain('No existing saves');
    expect(textContent(tree)).not.toContain('New fixed saves are unavailable');
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress(); });
    expect(mockListSavings).toHaveBeenCalledTimes(2);
    expect(textContent(tree)).toContain('Total locked');
    expect(textContent(tree)).toContain('No existing saves');
    expect(textContent(tree)).toContain('Savings are not available yet.');
  });

  it('preserves outstanding savings on refresh failure, marks them stale, and clears the error on retry', async () => {
    const saved = {
      ...emptySavings, total_locked: '12000.00',
      plans: [{ reference: 'old-plan', principal: '12000.00', maturity_value: '12100.00', rate: '0.12', duration_days: 30, status: 'active', matures_at: '2020-01-01' }],
    };
    mockListSavings.mockResolvedValueOnce(saved).mockResolvedValueOnce(offline).mockResolvedValueOnce(saved);
    const tree = await renderPage(<MySavings />); trees.push(tree);
    expect(textContent(tree)).toContain('Awaiting payout');
    expect(textContent(tree)).not.toContain('Maturing today');
    await act(async () => { tree.root.findByType(ScrollView).props.refreshControl.props.onRefresh(); });
    expect(textContent(tree)).toContain('Showing previously loaded savings');
    expect(textContent(tree)).toContain('₦12000');
    expect(textContent(tree)).toContain('₦12100');
    expect(textContent(tree)).not.toContain('No existing saves');
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress(); });
    expect(textContent(tree)).not.toContain('Showing previously loaded savings');
    expect(textContent(tree)).toContain('₦12000');
  });

  it('offers retry after a secure-store failure without displaying savings totals', async () => {
    mockGetToken.mockRejectedValueOnce(new Error('Keychain unavailable'));
    const tree = await renderPage(<MySavings />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to load your savings');
    expect(textContent(tree)).not.toContain('Total locked');
    expect(mockListSavings).not.toHaveBeenCalled();
    expect(tree.root.findByProps({ accessibilityLabel: 'Retry' })).toBeTruthy();
  });

  it('distinguishes a failed fixed-save availability check from a confirmed unavailable product', async () => {
    mockGetRates.mockResolvedValueOnce(offline).mockResolvedValueOnce({ success: true, product_available: false, unavailable_message: 'Savings are not available yet.' });
    const tree = await renderPage(<FixedSave />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to check savings availability');
    expect(textContent(tree)).not.toContain('Fixed savings unavailable');
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Continue' })).toHaveLength(0);
    expect(tree.root.findByProps({ accessibilityLabel: 'View existing saves' })).toBeTruthy();
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress(); });
    expect(textContent(tree)).toContain('Fixed savings unavailable');
    expect(textContent(tree)).not.toContain('Unable to check savings availability');
    expect(mockCreateSavings).not.toHaveBeenCalled();
  });

  it('keeps savings authorization unavailable when the rate response is incomplete', async () => {
    mockGetRates.mockResolvedValueOnce({ success: true, product_available: true, rates: [], min: '1000' });
    const tree = await renderPage(<FixedSave />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to check savings availability');
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Continue' })).toHaveLength(0);
    expect(mockCreateSavings).not.toHaveBeenCalled();
  });

  it('keeps authorization hidden during a slow availability retry and ignores duplicate retry taps', async () => {
    let resolveRetry!: (value: unknown) => void;
    const response = new Promise((resolve) => { resolveRetry = resolve; });
    mockGetRates.mockResolvedValueOnce(offline).mockReturnValueOnce(response);
    const tree = await renderPage(<FixedSave />); trees.push(tree);
    const retry = tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress;
    await act(async () => { retry(); retry(); });
    expect(mockGetRates).toHaveBeenCalledTimes(2);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Continue' })).toHaveLength(0);
    expect(textContent(tree)).not.toContain('Fixed savings unavailable');
    await act(async () => { resolveRetry({ success: true, product_available: false }); });
    expect(textContent(tree)).toContain('Fixed savings unavailable');
    expect(mockCreateSavings).not.toHaveBeenCalled();
  });

  it('distinguishes failed loan status from unavailability and preserves the active-loan guard after retry', async () => {
    mockGetLoanStatus.mockResolvedValueOnce(offline).mockResolvedValueOnce({
      success: true, product_available: false, available: '0.00', quote_rate: '0.00', active_loan: { reference: 'outstanding-loan' },
    });
    const tree = await renderPage(<GetLoan />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to check loan status');
    expect(textContent(tree)).not.toContain('New loans unavailable');
    expect(tree.root.findByProps({ accessibilityLabel: 'View loans' })).toBeTruthy();
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress(); });
    expect(textContent(tree)).toContain('You already have an active loan');
    expect(textContent(tree)).not.toContain('Unable to check loan status');
    expect(tree.root.findAll((node) => String(node.props.accessibilityLabel || '').startsWith('Get ₦'))).toHaveLength(0);
    expect(mockRequestLoan).not.toHaveBeenCalled();
  });

  it('does not authorize a loan when the successful status response omits its terms', async () => {
    mockGetLoanStatus.mockResolvedValueOnce({ success: true, product_available: true, available: '50000' });
    const tree = await renderPage(<GetLoan />); trees.push(tree);
    expect(textContent(tree)).toContain('Unable to check loan status');
    expect(tree.root.findAll((node) => String(node.props.accessibilityLabel || '').startsWith('Get ₦'))).toHaveLength(0);
    expect(mockRequestLoan).not.toHaveBeenCalled();
  });

  it('retries loan status into accessible selectors and disables presets above the credit limit', async () => {
    mockGetLoanStatus.mockResolvedValueOnce(offline).mockResolvedValueOnce({ success: true, product_available: true, available: '50000', quote_rate: '0.045', active_loan: null });
    const tree = await renderPage(<GetLoan />); trees.push(tree);
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry' }).props.onPress(); });
    const overLimit = tree.root.findAllByProps({ accessibilityLabel: '₦100000', accessibilityRole: 'radio' })[0];
    expect(overLimit.props.disabled).toBe(true);
    expect(overLimit.props.accessibilityState).toEqual({ selected: true, disabled: true });
    const tenure = tree.root.findAllByProps({ accessibilityLabel: '30 days', accessibilityRole: 'radio' })[0];
    expect(tenure.props.accessibilityState).toEqual({ selected: true });
  });
});
