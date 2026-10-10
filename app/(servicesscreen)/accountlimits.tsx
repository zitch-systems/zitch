import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { Screen, Header, Card, money, NText, Btn } from '@/components/design/ui';
import { Loading } from '@/components/design/Loading';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { apiJson } from '@/lib/api';
import { vasAccountStatusTitle, walletCapabilities, walletCapabilityMessage, walletService, type VirtualAccount } from '@/lib/services/wallet';

type Status = {
  tier: number;
  tier_name?: string;
  transaction_limit: string;
  daily_transfer_limit?: string;
  daily_bill_limit?: string;
  bvn_verified: boolean;
  nin_verified: boolean;
  bank_tier?: number;
  bank_tier_limits?: { single_inflow?: string | null; daily_spend?: string | null; max_balance?: string | null };
  account_provider?: 'partnership' | 'wema_vas';
};

const amountLabel = (value: string | undefined | null) => value != null
  && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0
  ? money(Number(value)) : 'Not confirmed';

/** 816 693 8327 — grouped for reading aloud. Copy still uses the raw digits. */
const grouped = (n: string) => {
  const d = (n || '').replace(/\D/g, '');
  return d.length === 10 ? `${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}` : d;
};

const AccountLimits = () => {
  const { c, theme } = useTheme();
  const [status, setStatus] = useState<Status | null>(null);
  const [fundingState, setFundingState] = useState<VirtualAccount | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const loadGeneration = useRef(0);

  const loadAccount = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadError('');
    const guard = setTimeout(() => {
      if (loadGeneration.current === generation) {
        setLoadError('Your account details are taking longer than expected. Check your connection and try again.');
        setLoading(false);
      }
    }, 8000);
    try {
      const [identity, funding] = await Promise.allSettled([apiJson('/api/kyc/status/'), walletService.getAccount()]);
      if (loadGeneration.current !== generation) return;
      if (identity.status !== 'fulfilled' || !identity.value?.success
          || funding.status !== 'fulfilled' || !funding.value?.success) {
        setLoadError('We could not load your current account details. Check your connection and try again.');
        return;
      }
      setStatus(identity.value as Status);
      setFundingState(funding.value);
      setLoadError('');
    } finally {
      clearTimeout(guard);
      if (loadGeneration.current === generation) setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => {
    void loadAccount();
    return () => { loadGeneration.current += 1; };
  }, [loadAccount]));

  const tier = status?.tier ?? 1;
  // The per-transaction ceiling the server will actually enforce today. The
  // ladder row is what the tier is *entitled* to; this is what it *has*, and on
  // an account mid-upgrade they are not the same number.
  const isVas = status?.account_provider === 'wema_vas' || fundingState?.provider === 'wema_vas';
  const capabilities = walletCapabilities(isVas ? { ...fundingState, provider: 'wema_vas' } : fundingState);
  const fundingReady = !!fundingState?.account_number && fundingState.available !== false
    && fundingState.has_account !== false && fundingState.test_mode !== true
    && !/^711/.test(fundingState.account_number)
    && (!isVas || (fundingState.available === true && fundingState.has_account === true
      && fundingState.account_setup_state === 'ready'));
  const displayedNumber = fundingReady ? fundingState?.account_number || '' : '';
  const displayedName = fundingReady ? fundingState?.account_name || '' : '';

  const copy = async () => {
    if (!displayedNumber) return;
    await Clipboard.setStringAsync(displayedNumber);
    notify('Copied', 'Your account number is on the clipboard.');
  };

  const linkedId = status
    ? [status.bvn_verified && 'BVN', status.nin_verified && 'NIN'].filter(Boolean).join(' & ') || 'Not linked'
    : '—';

  // This screen exists to answer "what is my tier and what can I send". Until
  // the status call lands, `tier ?? 1` and `limit ?? 0` answer it WRONG — a
  // Tier 3 customer opens their limits page and reads "Tier 1, ₦0". Hold the
  // brand loader for the first fetch instead of publishing a placeholder answer
  // to the only question the screen is for.
  if (loadError) {
    return (
      <Screen>
        <Header title="Account details" onBack={() => router.back()} />
        <Card>
          <Text style={{ color: c.ink1, fontFamily: font.bold, fontSize: 17 }}>Couldn&apos;t load account details</Text>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginVertical: 16 }}>{loadError}</Text>
          <Btn label="Try again" onPress={() => void loadAccount()} />
        </Card>
      </Screen>
    );
  }

  if (loading || !status) {
    return (
      <Screen>
        <Header title={isVas ? "Account details" : "Account Limits"} onBack={() => router.back()} />
        <Loading label={isVas ? "Checking your account…" : "Checking your limits…"} />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title={isVas ? "Account details" : "Account Limits"} onBack={() => router.back()} />

      {/* --- account + tier --- */}
      <LinearGradient
        colors={c.tierGradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ borderRadius: 24, padding: 20, marginBottom: 14, overflow: 'hidden' }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: theme === 'dark' ? '#E6D6AC' : '#7A6428' }}>
              Account Info
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 6 }}>
              <Text numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.72} style={{ flexShrink: 1, fontSize: 26, fontFamily: font.extrabold, color: theme === 'dark' ? '#FFF6DF' : '#2B2205', letterSpacing: -0.4 }}>
                {grouped(displayedNumber) || '—'}
              </Text>
              {!!displayedNumber && (
                <Pressable
                  onPress={copy}
                  accessibilityRole="button"
                  accessibilityLabel="Copy account number"
                  hitSlop={8}
                  style={{ width: 30, height: 30, borderRadius: 15, backgroundColor: 'rgba(255,255,255,.45)', alignItems: 'center', justifyContent: 'center' }}
                >
                  <ZIcon name="copy" size={15} color={theme === 'dark' ? '#FFF6DF' : '#5C4A18'} />
                </Pressable>
              )}
            </View>
            {!!displayedName && (
              <View style={{ alignSelf: 'flex-start', marginTop: 12, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.45)' }}>
                <Text numberOfLines={1} style={{ fontSize: 12, fontFamily: font.semibold, color: theme === 'dark' ? '#FFF6DF' : '#4A3B12' }}>
                  {displayedName.toUpperCase()}
                </Text>
              </View>
            )}
          </View>
          {/* Medal: the tier number in the middle of a ribboned disc. */}
          <View style={{ alignItems: 'center', justifyContent: 'center', width: 78, height: 78 }}>
            <ZIcon name="medal" size={74} color={theme === 'dark' ? 'rgba(255,246,223,.55)' : 'rgba(255,255,255,.75)'} stroke={1.6} />
            <View style={{ position: 'absolute', alignItems: 'center', paddingTop: 12 }}>
              <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: theme === 'dark' ? '#FFF6DF' : '#5C4A18', lineHeight: 24 }}>{tier}</Text>
              <Text style={{ fontSize: 9.5, fontFamily: font.bold, color: theme === 'dark' ? '#E6D6AC' : '#7A6428', letterSpacing: 0.4 }}>TIER</Text>
            </View>
          </View>
        </View>
      </LinearGradient>

      {/* --- linked id --- */}
      <Card
        style={{ marginBottom: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}
        onPress={() => router.push('/kyc')}
      >
        <Text style={{ flex: 1, fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Linked ID</Text>
        <Text style={{ fontSize: 14, fontFamily: font.semibold, color: linkedId === 'Not linked' ? c.ink3 : c.ink2 }}>{linkedId}</Text>
        <ZIcon name="right" size={17} color={c.ink3} />
      </Card>

      {isVas ? (
        <Card style={{ marginBottom: 24 }}>
          <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>{fundingReady ? 'Available services' : vasAccountStatusTitle(fundingState)}</Text>
          {!fundingReady && (fundingState?.enrollment_message || fundingState?.migration_message) ? (
            <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginTop: 10 }}>{fundingState.enrollment_message || fundingState.migration_message}</Text>
          ) : null}
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginTop: 10 }}>
            {walletCapabilityMessage(capabilities) || 'Bill payments and transfers are available.'}
          </Text>
          {!fundingReady ? <View style={{ marginTop: 14 }}><Btn label="Check account setup" onPress={() => router.push('/addmoney')} /></View> : null}
        </Card>
      ) : <>
      {!!fundingState?.migration_message && <Card style={{ marginBottom: 14 }}>
        <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{fundingState.migration_message}</Text>
      </Card>}
      {/* The backend transaction_limit is per transaction, not a daily limit. */}
      <Card style={{ marginBottom: 14 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Text style={{ flex: 1, fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Per-transaction limit</Text>
          <Pressable
            onPress={() => router.push('/kyc')}
            accessibilityRole="button"
            accessibilityLabel="Raise your limit"
            hitSlop={8}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
          >
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>{tier >= 3 ? 'Details' : 'Raise'}</Text>
            <ZIcon name="right" size={15} color={c.brand} />
          </Pressable>
        </View>
        <View style={{ borderRadius: 16, backgroundColor: c.surface2, padding: 14 }}>
          <NText style={{ fontSize: 20, fontFamily: font.bold, color: c.ink2 }}>{amountLabel(status.transaction_limit)}</NText>
        </View>
      </Card>
      <Card style={{ marginBottom: 14 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, marginBottom: 14 }}>Current daily limits</Text>
        {[
          ['Transfers', amountLabel(status.daily_transfer_limit)],
          ['Bill payments', amountLabel(status.daily_bill_limit)],
          ['Bank daily spending', amountLabel(status.bank_tier_limits?.daily_spend)],
          ['Single incoming transfer', amountLabel(status.bank_tier_limits?.single_inflow)],
          ['Account balance ceiling', amountLabel(status.bank_tier_limits?.max_balance)],
        ].map(([label, value]) => <View key={label} style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 12, paddingVertical: 9 }}>
          <Text style={{ flex: 1, color: c.ink2, fontFamily: font.regular }}>{label}</Text>
          <NText style={{ color: c.ink1, fontFamily: font.semibold }}>{value}</NText>
        </View>)}
        <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, lineHeight: 18, marginTop: 12 }}>These are your current confirmed limits. Your personal limit and bank restrictions also apply.</Text>
      </Card>
      <Card style={{ marginBottom: 24 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, marginBottom: 14 }}>Verification steps</Text>
        {['Tier 1 · BVN or NIN, verified by SMS code or face', 'Tier 2 · Prembly face verification and bank upgrade', 'Tier 3 · Address verification'].map((label) => <Text key={label} style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 22, marginBottom: 8 }}>{label}</Text>)}
        <Btn label="View verification" variant="ghost" onPress={() => router.push('/kyc')} />
      </Card>
      </>}
    </Screen>
  );
};

export default AccountLimits;
