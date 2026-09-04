import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { getToken, saveDisplayName } from '@/lib/secureStore';
import { apiPost, apiJson } from '@/lib/api';
import type { Txn } from '@/components/design/ui';

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
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(s.trim());
  if (!m) {
    const loose = Date.parse(s);
    return Number.isNaN(loose) ? undefined : loose;
  }
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
};

// Picks an icon from the service label. Direction comes from the backend's
// authoritative `direction` field; the label regex is only a fallback.
const mapTxn = (raw: any, i: number): Txn => {
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
  return {
    id: String(raw?.id ?? raw?.reference ?? i),
    type: service,
    detail: when,
    ts: parseTs(when),
    amount: Number(raw?.amount ?? 0),
    status: String(raw?.transaction_status ?? 'Successful'),
    icon,
    dir: raw?.direction === 'in' || raw?.direction === 'out' ? raw.direction : inflow ? 'in' : 'out',
    reference: String(raw?.reference ?? ''),
    narration: String(raw?.narration ?? ''),
  };
};

type WalletValue = {
  balance: number;
  firstName: string;
  avatar: string;
  accountNumber: string;
  phoneNumber: string;
  /** The full name the bank holds for the wallet's NUBAN. `firstName` is a
   *  greeting; this is the legal name a receipt has to print. */
  accountName: string;
  bankName: string;
  txns: Txn[];
  loading: boolean;
  showBal: boolean;
  setShowBal: (v: boolean) => void;
  reload: () => Promise<void>;
  linked: LinkedAccount[];
  reloadLinked: () => Promise<void>;
};

const WalletContext = createContext<WalletValue>({
  balance: 0,
  firstName: '',
  avatar: '',
  accountNumber: '',
  phoneNumber: '',
  accountName: '',
  bankName: '',
  txns: [],
  loading: true,
  showBal: true,
  setShowBal: () => {},
  reload: () => Promise.resolve(),
  linked: [],
  reloadLinked: () => Promise.resolve(),
});

export const WalletProvider = ({ children }: { children: React.ReactNode }) => {
  const [balance, setBalance] = useState(0);
  const [firstName, setFirstName] = useState('');
  const [avatar, setAvatar] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [accountName, setAccountName] = useState('');
  const [bankName, setBankName] = useState('');
  const [txns, setTxns] = useState<Txn[]>([]);
  const [loading, setLoading] = useState(true);
  const [showBal, setShowBal] = useState(true);
  const [linked, setLinked] = useState<LinkedAccount[]>([]);

  // The user's Mono-linked external bank accounts (display + funding source).
  // Loaded alongside the wallet and refreshable on demand (reloadLinked).
  const reloadLinked = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) return;
      const r = await apiJson<{ accounts?: any[] }>('/api/banklink/list/');
      const list = Array.isArray(r?.accounts) ? r.accounts : [];
      setLinked(list.map((a) => ({
        id: Number(a.id),
        bank_name: String(a.bank_name ?? ''),
        account_number: String(a.account_number ?? ''),
        account_name: String(a.account_name ?? ''),
        balance: a.balance == null || a.balance === '' ? null : Number(a.balance),
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

    const run = (async () => {
      setLoading(true);
      try {
        const token = await getToken();
        if (!token) return;
        const [balRes, txRes] = await Promise.allSettled([
          apiPost('/api/wallet_balance/').then((response) => response.json()),
          apiPost('/api/user-transaction-history/').then((response) => response.json()),
        ]);

        if (balRes.status === 'fulfilled' && balRes.value?.success) {
          const value = balRes.value;
          setBalance(Number(value.wallet ?? 0));
          const named = String(value.user_first_name || value.user_last_name
            || String(value.user_email || '').split('@')[0] || '');
          setFirstName(named);
          void saveDisplayName(named);
          setAvatar(String(value.user_avatar ?? ''));
          setAccountNumber(String(value.account_number ?? ''));
          setPhoneNumber(String(value.user_phone_number ?? ''));
          setAccountName(String(value.account_name ?? ''));
          setBankName(String(value.bank_name ?? ''));
        }
        if (txRes.status === 'fulfilled' && txRes.value?.status) {
          const list = Array.isArray(txRes.value.all_site_transactions)
            ? txRes.value.all_site_transactions
            : [];
          setTxns(list.map(mapTxn));
        }
      } catch {
        // Keep last-known values visible through transient network failures.
      } finally {
        setLoading(false);
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
    () => ({ balance, firstName, avatar, accountNumber, phoneNumber, accountName, bankName, txns, loading, showBal, setShowBal, reload: load, linked, reloadLinked }),
    [balance, firstName, avatar, accountNumber, phoneNumber, accountName, bankName, txns, loading, showBal, load, linked, reloadLinked],
  );

  return <WalletContext.Provider value={value}>{children}</WalletContext.Provider>;
};

export const useWallet = () => useContext(WalletContext);
