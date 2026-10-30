import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Btn } from '@/components/design/ui';
import { banking, type BankStatus } from '@/lib/services/banking';
import { isFundingAccountReady } from '@/lib/services/wallet';
import { getSessionGeneration } from '@/lib/secureStore';
import { useTheme, font } from '@/lib/theme';

function AddMoneyContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(getSessionGeneration());
  const request = useRef(0);
  const current = () => generation.current === getSessionGeneration();
  const load = async () => {
    const serial = ++request.current;
    setBusy(true); setMessage('');
    try {
      const result = await banking.status();
      if (!current() || serial !== request.current) return;
      setStatus(result.success ? result : null);
      setMessage(result.success ? '' : result.message || 'Could not load your account. Please try again.');
    } catch {
      if (current() && serial === request.current) {
        setStatus(null); setMessage('Could not load your account. Please try again.');
      }
    } finally { if (current() && serial === request.current) setBusy(false); }
  };
  useEffect(() => { generation.current = getSessionGeneration(); void load(); return () => { generation.current = -1; }; }, []);
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 24 };
  return <Screen header={<Header title="Add money" />}><View style={{ gap: 18 }}>
    {status?.sandbox && <Text style={text}>Test environment. Do not transfer real money to a test account.</Text>}
    {status && isFundingAccountReady(status) ? <>
      <Text style={text}>Transfer to your dedicated 9PSB account. Your wallet updates after the bank confirms the deposit.</Text>
      <Text style={text}>Bank: 9PSB</Text><Text style={text}>Name: {status.account_name}</Text>
      <Text selectable style={{ ...text, fontSize: 26 }}>{status.account_number}</Text>
      <Btn label="Copy account number" onPress={async () => {
        if (!current() || !isFundingAccountReady(status)) return;
        try { await Clipboard.setStringAsync(status?.account_number || ''); }
        catch { if (current()) setMessage('Could not copy your account number. Please try again.'); }
      }} />
      {status.maximum_single_deposit != null && <Text style={text}>Maximum single deposit: ₦{Number(status.maximum_single_deposit).toLocaleString()}</Text>}
      {status.maximum_balance != null && <Text style={text}>Maximum balance: ₦{Number(status.maximum_balance).toLocaleString()}</Text>}
      <Text style={text}>The bank may restrict outgoing payments if an incoming deposit exceeds your tier's limit.</Text>
    </> : status ? <><Text style={text}>{status.migration_message || 'Complete your 9PSB identity and account setup to receive transfers.'}</Text>
      {status.identity_available === true && <Btn label="Set up my account" onPress={() => router.push('/(auth)/kyc')} />}
    </> : busy ? <Text style={text}>Loading account details…</Text> : null}
    {!!message && <Text style={text}>{message}</Text>}
    <Btn label={busy ? 'Refreshing…' : 'Refresh account'} variant="outline" disabled={busy} onPress={() => void load()} />
  </View></Screen>;
}

export default function AddMoney() {
  return <AuthGuard><AddMoneyContent /></AuthGuard>;
}
