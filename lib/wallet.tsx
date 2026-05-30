import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { getToken } from '@/lib/secureStore';
import baseUrl from '@/components/configFiles/apiConfig';
import type { Txn } from '@/components/design/ui';

// Picks an icon + direction for a transaction from its service label.
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
    detail: String(raw?.transaction_status ?? raw?.date ?? 'Successful'),
    amount: Number(raw?.amount ?? 0),
    status: String(raw?.transaction_status ?? 'Successful'),
    icon,
    dir: inflow ? 'in' : 'out',
  };
};

type WalletValue = {
  balance: number;
  firstName: string;
  txns: Txn[];
  loading: boolean;
  showBal: boolean;
  setShowBal: (v: boolean) => void;
  reload: () => void;
};

const WalletContext = createContext<WalletValue>({
  balance: 0,
  firstName: '',
  txns: [],
  loading: true,
  showBal: true,
  setShowBal: () => {},
  reload: () => {},
});

export const WalletProvider = ({ children }: { children: React.ReactNode }) => {
  const [balance, setBalance] = useState(0);
  const [firstName, setFirstName] = useState('');
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
        fetch(`${baseUrl}/api/wallet_balance/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: token }),
        }).then((r) => r.json()),
        fetch(`${baseUrl}/api/user-transaction-history/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: token }),
        }).then((r) => r.json()),
      ]);

      if (balRes.status === 'fulfilled' && balRes.value?.success) {
        setBalance(Number(balRes.value.wallet ?? 0));
        setFirstName(String(balRes.value.user_first_name ?? balRes.value.user_last_name ?? ''));
      }
      if (txRes.status === 'fulfilled' && txRes.value?.status) {
        const list = txRes.value.all_site_transactions ?? [];
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

  return (
    <WalletContext.Provider value={{ balance, firstName, txns, loading, showBal, setShowBal, reload: load }}>
      {children}
    </WalletContext.Provider>
  );
};

export const useWallet = () => useContext(WalletContext);
