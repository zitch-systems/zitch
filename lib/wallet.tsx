import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import type { Txn } from '@/components/design/ui';

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
  return {
    id: String(raw?.id ?? raw?.reference ?? i),
    type: service,
    detail: String(raw?.date ?? raw?.created_at ?? raw?.time ?? ''),
    amount: Number(raw?.amount ?? 0),
    status: String(raw?.transaction_status ?? 'Successful'),
    icon,
    dir: raw?.direction === 'in' || raw?.direction === 'out' ? raw.direction : inflow ? 'in' : 'out',
    reference: String(raw?.reference ?? ''),
  };
};

type WalletValue = {
  balance: number;
  firstName: string;
  avatar: string;
  accountNumber: string;
  bankName: string;
  txns: Txn[];
  loading: boolean;
  showBal: boolean;
  setShowBal: (v: boolean) => void;
  reload: () => void;
};

const WalletContext = createContext<WalletValue>({
  balance: 0,
  firstName: '',
  avatar: '',
  accountNumber: '',
  bankName: '',
  txns: [],
  loading: true,
  showBal: true,
  setShowBal: () => {},
  reload: () => {},
});

export const WalletProvider = ({ children }: { children: React.ReactNode }) => {
  const [balance, setBalance] = useState(0);
  const [firstName, setFirstName] = useState('');
  const [avatar, setAvatar] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [bankName, setBankName] = useState('');
  const [txns, setTxns] = useState<Txn[]>([]);
  const [loading, setLoading] = useState(true);
  const [showBal, setShowBal] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = await getToken();
      if (!token) {
        setLoading(false);
        return;
      }
      const [balRes, txRes] = await Promise.allSettled([
        apiPost('/api/wallet_balance/').then((r) => r.json()),
        apiPost('/api/user-transaction-history/').then((r) => r.json()),
      ]);

      if (balRes.status === 'fulfilled' && balRes.value?.success) {
        setBalance(Number(balRes.value.wallet ?? 0));
        setFirstName(String(balRes.value.user_first_name ?? balRes.value.user_last_name ?? ''));
        setAvatar(String(balRes.value.user_avatar ?? ''));
        setAccountNumber(String(balRes.value.account_number ?? ''));
        setBankName(String(balRes.value.bank_name ?? ''));
      }
      if (txRes.status === 'fulfilled' && txRes.value?.status) {
        const list = Array.isArray(txRes.value.all_site_transactions) ? txRes.value.all_site_transactions : [];
        setTxns(list.map(mapTxn));
      }
    } catch {
      // surfaced to the user elsewhere; keep the dashboard usable on failure
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Memoize so the context value is stable between renders — otherwise every
  // wallet consumer (Home, Wallet, the tab bar, service screens) re-renders
  // whenever the provider renders, even when nothing it reads has changed.
  const value = useMemo(
    () => ({ balance, firstName, avatar, accountNumber, bankName, txns, loading, showBal, setShowBal, reload: load }),
    [balance, firstName, avatar, accountNumber, bankName, txns, loading, showBal, load],
  );

  return <WalletContext.Provider value={value}>{children}</WalletContext.Provider>;
};

export const useWallet = () => useContext(WalletContext);
