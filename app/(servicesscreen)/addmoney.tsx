import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Btn } from '@/components/design/ui';
import { banking, type BankStatus } from '@/lib/services/banking';
import { getSessionGeneration } from '@/lib/secureStore';
import { useTheme, font } from '@/lib/theme';

function AddMoneyContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const generation = useRef(getSessionGeneration());
  const load = async () => {
    setBusy(true); const result = await banking.status();
    if (generation.current !== getSessionGeneration()) return;
    setStatus(result.success ? result : null); setMessage(result.success ? '' : result.message || 'Could not load your account.'); setBusy(false);
  };
  useEffect(() => { generation.current = getSessionGeneration(); void load(); return () => { generation.current = -1; }; }, []);
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 24 };
  return <Screen header={<Header title="Add money" />}><View style={{ gap: 18 }}>
    {status?.sandbox && <Text style={text}>Test environment. Do not transfer real money to a test account.</Text>}
    {status?.account_number && status.account_setup_state === 'ready' ? <>
      <Text style={text}>Transfer to your dedicated 9PSB account. Your wallet updates after the bank confirms the deposit.</Text>
      <Text style={text}>Bank: 9PSB</Text><Text style={text}>Name: {status.account_name}</Text>
      <Text selectable style={{ ...text, fontSize: 26 }}>{status.account_number}</Text>
      <Btn label="Copy account number" onPress={() => void Clipboard.setStringAsync(status.account_number || '')} />
      {status.maximum_single_deposit != null && <Text style={text}>Maximum single deposit: ₦{Number(status.maximum_single_deposit).toLocaleString()}</Text>}
      {status.maximum_balance != null && <Text style={text}>Maximum balance: ₦{Number(status.maximum_balance).toLocaleString()}</Text>}
      <Text style={text}>The bank may restrict outgoing payments if an incoming deposit exceeds your tier's limit.</Text>
    </> : <><Text style={text}>Complete your 9PSB identity and account setup to receive transfers.</Text><Btn label="Set up my account" onPress={() => router.push('/(auth)/kyc')} /></>}
    {!!message && <Text style={text}>{message}</Text>}
    <Btn label={busy ? 'Refreshing…' : 'Refresh account'} variant="outline" disabled={busy} onPress={() => void load()} />
  </View></Screen>;
}

export default function AddMoney() {
  return <AuthGuard><AddMoneyContent /></AuthGuard>;
}
