import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { getToken, getSessionGeneration, saveDisplayName, saveSpendAccountNamespace } from '@/lib/secureStore';
import { apiPost, apiJson } from '@/lib/api';
import type { Txn } from '@/components/design/ui';
import { walletBalances, walletCapabilities } from '@/lib/services/wallet';

// An external bank account the user linked via Mono open banking. Mirrors the
// backend banklink.views._serialize shape (balance is display-only/cached).
export type LinkedAccount = {
  id: number;
  bank_name: string;
  account_number: string; // masked by the backend (****1234)
  account_name: string;
  balance: number | null;
  balance_updated: string | null;
  status: string; // 'active' | 'reauth' | ...
  mono_account_id?: string;
};

// The backend sends "YYYY-MM-DD HH:MM" (wallet.views.transaction_history).
// Parsed by hand rather than handed to `new Date(str)`: that form is not in the
// ECMAScript spec's grammar, so Hermes and JSC are free to disagree about it —
// and one of them reads it as UTC, which silently shifts a late-evening
// transaction into the next day's group. Returns undefined when unreadable, and
// the caller buckets those separately rather than inventing a date.
const parseTs = (s: string): number | undefined => {
  const value = s.trim();
  // Preserve explicit UTC/offset information from ISO API responses.
  if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(value);
  if (!m) return undefined;
  const parts = m.slice(1, 7).map((part) => Number(part ?? 0));
  const [year, month, day, hour, minute, second] = parts;
  const date = new Date(year, month - 1, day, hour, minute, second, Number((m[7] ?? '').padEnd(3, '0')));
  // Date silently normalizes 31 February and 25:00. Keep invalid rows undated.
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
    && date.getHours() === hour && date.getMinutes() === minute && date.getSeconds() === second
    ? date.getTime() : undefined;
};

// Picks an icon from the service label. Direction comes from the backend's
// authoritative `direction` field; the label regex is only a fallback.
export const mapTxn = (raw: any, i: number): Txn => {
  const service = String(raw?.service ?? raw?.type ?? 'Transaction');
  const s = service.toLowerCase();
  const inflow = /fund|deposit|refund|cashback|credit|received/.test(
    s + ' ' + String(raw?.transaction_status ?? '')
  );
  let icon = 'wallet';
  if (/airtime/.test(s)) icon = 'airtime';
  else if (/data/.test(s)) icon = 'data';
  else if (/cable|tv|dstv|gotv|startime/.test(s)) icon = 'tv';
  else if (/elect|power|disco/.test(s)) icon = 'bills';
  else if (/transfer|send|withdraw/.test(s)) icon = 'send';
  else if (/fund|deposit|add/.test(s)) icon = 'deposit';
  const when = String(raw?.date ?? raw?.created_at ?? raw?.time ?? '');
  const underReview = raw?.under_review === true;
  return {
    id: String(raw?.id ?? raw?.reference ?? i),
    type: service,
    detail: when,
    ts: parseTs(when),
    amount: Number(raw?.amount ?? 0),
    // Missing/unknown ledger status is never proof of settlement. The shared
    // classifier renders this conservatively rather than painting it green.
    status: underReview
      ? 'Under review'
      : String(raw?.transaction_status ?? 'Status unavailable'),
    icon,
    dir: raw?.direction === 'in' || raw?.direction === 'out' ? raw.direction : inflow ? 'in' : 'out',
    reference: String(raw?.reference ?? ''),
    narration: String(raw?.narration ?? ''),
    underReview,
    statusMessage: underReview ? String(raw?.status_message ?? '') : '',
    reviewKind: underReview ? String(raw?.review_kind ?? '') : '',
  };
};

/** Carry review guidance consistently from every activity entry point. */
export const transactionParams = (txn: Txn) => ({
  type: txn.type, amount: String(txn.amount), status: txn.status, dir: txn.dir,
  detail: txn.detail, reference: txn.reference ?? '', icon: txn.icon,
  narration: txn.narration ?? '', underReview: txn.underReview ? '1' : '',
  statusMessage: txn.statusMessage ?? '', reviewKind: txn.reviewKind ?? '',
});

type WalletValue = {
  /** Spendable funds for existing purchase consumers; never the VAS aggregate. */
  balance: number;
  availableBalance: number;
  totalBalance: number;
  historicalBalance: number;
  fundingProvider?: 'partnership' | 'wema_vas';
  firstName: string;
  fullName: string;
  avatar: string;
  accountNumber: string;
  phoneNumber: string;
  /** The full name the bank holds for the wallet's NUBAN. `firstName` is a
   *  greeting; this is the legal name a receipt has to print. */
  accountName: string;
  bankName: string;
  spendingAvailable: boolean;
  billPaymentsAvailable: boolean;
  transfersAvailable: boolean;
  fundingMessage: string;
  txns: Txn[];
  loading: boolean;
  /** True once the first load ATTEMPT has finished, success or not.
   *
   *  Distinct from `loading`, and the distinction is the whole point: `loading`
   *  goes true again on every focus refresh, so a screen that drew skeletons
   *  from it would flash placeholders over content the customer is already
   *  reading every time they navigated back. `hydrated` latches once and stays
   *  true, which is what "show the skeleton only before there has ever been
   *  data" actually needs. Refreshes after that are the pull-to-refresh
   *  spinner's job, not the skeleton's. */
  hydrated: boolean;
  balanceLoaded: boolean;
  balanceError: string;
  historyError: string;
  showBal: boolean;
  setShowBal: (v: boolean) => void;
  reload: () => Promise<void>;
  linked: LinkedAccount[];
  reloadLinked: () => Promise<void>;
};

const WalletContext = createContext<WalletValue>({
  balance: 0,
  availableBalance: 0,
  totalBalance: 0,
  historicalBalance: 0,
  firstName: '',
  fullName: '',
  avatar: '',
  accountNumber: '',
  phoneNumber: '',
  accountName: '',
  bankName: '',
  spendingAvailable: false,
  billPaymentsAvailable: false,
  transfersAvailable: false,
  fundingMessage: '',
  txns: [],
  loading: true,
  hydrated: false,
  balanceLoaded: false,
  balanceError: '',
  historyError: '',
  showBal: true,
  setShowBal: () => {},
  reload: () => Promise.resolve(),
  linked: [],
  reloadLinked: () => Promise.resolve(),
});

export const WalletProvider = ({ children }: { children: React.ReactNode }) => {
  const [balances, setBalances] = useState(() => walletBalances(undefined));
  const [fundingProvider, setFundingProvider] = useState<'partnership' | 'wema_vas'>();
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [avatar, setAvatar] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [accountName, setAccountName] = useState('');
  const [bankName, setBankName] = useState('');
  const [spendingAvailable, setSpendingAvailable] = useState(false);
  const [capabilities, setCapabilities] = useState({ billPaymentsAvailable: false, transfersAvailable: false });
  const [fundingMessage, setFundingMessage] = useState('');
  const [txns, setTxns] = useState<Txn[]>([]);
  const [loading, setLoading] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [balanceLoaded, setBalanceLoaded] = useState(false);
  const [balanceError, setBalanceError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [showBal, setShowBal] = useState(true);
  const [linked, setLinked] = useState<LinkedAccount[]>([]);

  // The user's Mono-linked external bank accounts (display + funding source).
  // Loaded alongside the wallet and refreshable on demand (reloadLinked).
  const reloadLinked = useCallback(async () => {
    const generation = getSessionGeneration();
    try {
      const token = await getToken();
      if (!token) return;
      const r = await apiJson<{ success?: boolean; accounts?: any[] }>('/api/banklink/list/');
      if (getSessionGeneration() !== generation || r?.success === false || !Array.isArray(r?.accounts)) return;
      const list = r.accounts;
      setLinked(list.map((a) => ({
        id: Number(a.id),
        bank_name: String(a.bank_name ?? ''),
        account_number: String(a.account_number ?? ''),
        account_name: String(a.account_name ?? ''),
        balance: a.balance == null || a.balance === '' || !Number.isFinite(Number(a.balance)) ? null : Number(a.balance),
        balance_updated: a.balance_updated ?? null,
        status: String(a.status ?? 'active'),
        mono_account_id: a.mono_account_id ? String(a.mono_account_id) : undefined,
      })));
    } catch {
      // keep last-known list; transient failures shouldn't blank the UI
    }
  }, []);

  // Several tab focus effects can fire during the same navigation transition.
  // Coalesce them into one balance/history request pair so the API, JSON parser and
  // React tree do the work once rather than two or three times in parallel.
  const loadInFlight = useRef<Promise<void> | null>(null);

  const load = useCallback((): Promise<void> => {
    if (loadInFlight.current) return loadInFlight.current;

    const generation = getSessionGeneration();
    const run = (async () => {
      setLoading(true);
      try {
        const token = await getToken();
        if (!token || getSessionGeneration() !== generation) return;
        const [balRes, txRes] = await Promise.allSettled([
          apiPost('/api/wallet_balance/').then((response) => response.json()),
          apiPost('/api/user-transaction-history/').then((response) => response.json()),
        ]);

        if (getSessionGeneration() !== generation) return;
        if (balRes.status === 'fulfilled' && balRes.value?.success === true) {
          const value = balRes.value;
          if (value.account_namespace) {
            await saveSpendAccountNamespace(String(value.account_namespace));
            if (getSessionGeneration() !== generation) return;
          }
          setBalances(walletBalances(value));
          setBalanceLoaded(true);
          setBalanceError('');
          setFundingProvider(value.provider);
          const first = String(value.user_first_name || '');
          const last = String(value.user_last_name || '');
          const named = String(first || last
            || String(value.user_email || '').split('@')[0] || '');
          setFirstName(first || named);
          setLastName(last);
          void saveDisplayName(named).catch(() => {});
          setAvatar(String(value.user_avatar ?? ''));
          const fundable = value.provider !== 'wema_vas' ||
            (value.test_mode !== true && !/^711/.test(String(value.account_number ?? ''))
              && value.available === true && value.has_account === true && value.account_setup_state === 'ready');
          const account = String(value.account_number ?? '');
          setAccountNumber(fundable && /^\d{10}$/.test(account) && !/^711/.test(account) ? account : '');
          setSpendingAvailable(value.provider === 'wema_vas' && value.test_mode === true ? false : value.spending_available !== false);
          setCapabilities(walletCapabilities(value));
          setFundingMessage(String(value.migration_message ?? ''));
          setPhoneNumber(String(value.user_phone_number ?? ''));
          setAccountName(String(value.account_name ?? ''));
          setBankName(String(value.bank_name ?? ''));
        } else {
          setBalanceError('Could not refresh your balance. Check your connection and try again.');
        }
        if (txRes.status === 'fulfilled' && txRes.value?.status === true) {
          setHistoryError('');
          const list = Array.isArray(txRes.value.all_site_transactions)
            ? txRes.value.all_site_transactions
            : [];
          setTxns(list.map(mapTxn));
        } else {
          setHistoryError('Could not refresh your transactions. Check your connection and try again.');
        }
      } catch {
        if (getSessionGeneration() !== generation) return;
        setBalanceError('Could not refresh your balance. Check your connection and try again.');
        setHistoryError('Could not refresh your transactions. Check your connection and try again.');
      } finally {
        if (getSessionGeneration() !== generation) return;
        setLoading(false);
        // Latched on the attempt, not on success: a first load that fails
        // offline must stop showing skeletons and fall through to the real
        // empty/error state, not shimmer indefinitely at someone with no signal.
        setHydrated(true);
      }
    })();

    loadInFlight.current = run;
    void run.finally(() => {
      if (loadInFlight.current === run) loadInFlight.current = null;
    });
    return run;
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
      void reloadLinked();
    }, 0);
    return () => clearTimeout(timer);
  }, [load, reloadLinked]);

  // Memoize so the context value is stable between renders — otherwise every
  // wallet consumer (Home, Wallet, the tab bar, service screens) re-renders
  // whenever the provider renders, even when nothing it reads has changed.
  const value = useMemo(
    () => ({ balance: balances.availableBalance, ...balances, fundingProvider, firstName, fullName: accountName || `${firstName} ${lastName}`.trim(), avatar, accountNumber, phoneNumber, accountName, bankName, spendingAvailable, ...capabilities, fundingMessage, txns, loading, hydrated, balanceLoaded, balanceError, historyError, showBal, setShowBal, reload: load, linked, reloadLinked }),
    [balances, fundingProvider, firstName, lastName, avatar, accountNumber, phoneNumber, accountName, bankName, spendingAvailable, capabilities, fundingMessage, txns, loading, hydrated, balanceLoaded, balanceError, historyError, showBal, load, linked, reloadLinked],
  );

  return <WalletContext.Provider value={value}>{children}</WalletContext.Provider>;
};

export const useWallet = () => useContext(WalletContext);
