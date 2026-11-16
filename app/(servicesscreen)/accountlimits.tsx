import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { Screen, Header, Card, money, NText, Btn, Sheet } from '@/components/design/ui';
import { Loading } from '@/components/design/Loading';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { groupAccountNumber } from '@/lib/format';
import { apiJson } from '@/lib/api';
import { tierLadder, type LadderTier } from '@/lib/tierLadder';
import { TierBadge, TierDetails, TierPill } from '@/components/design/TierRow';
import { isFundingAccountReady, walletCapabilities, walletCapabilityMessage, walletService, type FundingAccount } from '@/lib/services/wallet';

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
  account_provider?: string;
  address?: string;
  address_verified?: boolean;
  address_verification_pending?: boolean;
  tier_ladder?: LadderTier[];
};

/** The amber of the Tier 3 "in progress" pill and sheet (design, Nov 2026). */
const AMBER = '#B45309';

type Tier3Sheet = null | { kind: 'progress' | 'done' | 'unavailable'; message?: string };

const amountLabel = (value: unknown) => (typeof value === 'string' || typeof value === 'number')
  && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0
  ? money(Number(value)) : 'Not confirmed';

/** 816 693 8327 — grouped for reading aloud. Copy still uses the raw digits. */
const grouped = (n: string) => groupAccountNumber((n || '').replace(/\D/g, ''));

const AccountLimits = () => {
  const { c, theme } = useTheme();
  const [status, setStatus] = useState<Status | null>(null);
  const [fundingState, setFundingState] = useState<FundingAccount | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const loadGeneration = useRef(0);
  const [tier3, setTier3] = useState<Tier3Sheet>(null);
  const [tier3Busy, setTier3Busy] = useState(false);

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
  const capabilities = walletCapabilities(fundingState);
  const fundingReady = isFundingAccountReady(fundingState);
  const accountReview = fundingState?.provider !== 'partnership' || fundingState.legacy_funding_review === true
    || fundingState.account_setup_state === 'legacy_funding_review';
  const displayedNumber = fundingReady ? fundingState?.account_number || '' : '';
  const displayedName = fundingReady ? fundingState?.account_name || '' : '';

  const copy = async () => {
    if (!displayedNumber) return;
    try {
      await Clipboard.setStringAsync(displayedNumber);
      notify('Copied', 'Your account number is on the clipboard.');
    } catch { notify('Could not copy', 'Please try again.'); }
  };

  // Tier 3 is the address given at sign-up. The server only marks it verified
  // when the bank confirms that address, so the answer here is whatever the
  // server says: done, in progress, or not available yet. Nothing is assumed.
  const verifyTier3 = async () => {
    if (tier3Busy) return;
    setTier3Busy(true);
    try {
      const r: any = await apiJson('/api/kyc/address/');
      if (r?.success && r.address_verified) {
        setStatus((s) => (s ? { ...s, ...r } : s));
        setTier3({ kind: 'done' });
      } else if (r?.pending || r?.address_verification_pending) {
        setTier3({ kind: 'progress', message: r?.message });
      } else if (r?.upgrade_required) {
        notify('Verify Tier 2 first', r?.message || 'Complete Tier 2 before your address can be checked.');
      } else {
        setTier3({ kind: 'unavailable', message: r?.message });
      }
    } catch {
      notify('Could not check', 'Check your connection and try again.');
    } finally {
      setTier3Busy(false);
    }
  };

  const tint = theme === 'dark' ? 'rgba(234,251,247,0.07)' : 'rgba(6,110,102,0.06)';
  const ladder = tierLadder(status?.tier_ladder);
  const tier3Limits = ladder.find((t) => t.tier === 3);

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
        <Header title="Account Limits" onBack={() => router.back()} />
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
        <Header title="Account Limits" onBack={() => router.back()} />
        <Loading label="Checking your limits…" />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Account Limits" onBack={() => router.back()} />

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

      {accountReview ? (
        <Card style={{ marginBottom: 24 }}>
          <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Account review</Text>
          {fundingState?.migration_message ? (
            <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginTop: 10 }}>{fundingState.migration_message}</Text>
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
      {/* Today's daily caps as two tiles (review, Nov 2026: five rows became two). */}
      <Card style={{ marginBottom: 14 }}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, marginBottom: 10 }}>Current daily limits</Text>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          {[['Transfers', amountLabel(status.daily_transfer_limit)], ['Bill payments', amountLabel(status.daily_bill_limit)]].map(([label, value]) => (
            <View key={label} style={{ flex: 1, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: tint }}>
              <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>{label}</Text>
              <NText style={{ fontSize: 16, marginTop: 2, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{value}</NText>
            </View>
          ))}
        </View>
        <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, lineHeight: 17, marginTop: 10 }}>Your own limit and bank rules also apply.</Text>
      </Card>

      {/* The tier ladder: what each tier needs and allows, where you are, and the next step. */}
      <Card style={{ marginBottom: 24 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <Text style={{ fontSize: 16, fontFamily: font.extrabold, color: c.ink1 }}>Account tiers</Text>
          <Text style={{ fontSize: 12, fontFamily: font.medium, color: c.ink3 }}>Higher tier, higher limits</Text>
        </View>
        {ladder.map((t, i) => {
          const done = t.tier <= tier;
          const isCur = t.tier === tier;
          const last = i === ladder.length - 1;
          const isNext = t.tier === tier + 1;
          const locked = !done && t.tier > tier + 1;
          const pending = t.tier === 3 && !done && status.address_verification_pending;
          return (
            <View key={t.tier} style={{ flexDirection: 'row', gap: 14 }}>
              <View style={{ alignItems: 'center', width: 36 }}>
                <TierBadge tier={t.tier} done={done} current={isCur} />
                {last ? null : <View style={{ flex: 1, width: 2, marginVertical: 4, backgroundColor: t.tier < tier ? '#00A01A' : c.line }} />}
              </View>
              <View style={{ flex: 1, paddingBottom: last ? 0 : 20 }}>
                <TierDetails t={t} done={done} pill={done ? <TierPill label={isCur ? 'Your tier' : 'Verified'} tone={isCur ? 'current' : 'verified'} /> : null} />
                {done ? null : pending ? (
                  // Tier 3 is already being checked: a status, not a button.
                  <View accessibilityLabel="Tier 3 in progress" style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 12, minHeight: 40, borderRadius: 999, backgroundColor: 'rgba(217,119,6,0.10)' }}>
                    <ZIcon name="history" size={15} color={AMBER} stroke={2.2} />
                    <Text style={{ fontSize: 13, fontFamily: font.bold, color: AMBER }}>In progress</Text>
                  </View>
                ) : locked ? (
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 12, minHeight: 40, borderRadius: 999, backgroundColor: c.surface2 }}>
                    <ZIcon name="lock" size={15} color={c.ink3} />
                    <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink3 }}>Unlocks after Tier {t.tier - 1}</Text>
                  </View>
                ) : (
                  <Pressable
                    onPress={() => (t.tier === 3 ? void verifyTier3() : t.tier === 2 ? router.push('/tier2?from=limits') : router.push('/kyc'))}
                    disabled={t.tier === 3 && tier3Busy}
                    accessibilityRole="button"
                    accessibilityLabel={`Verify Tier ${t.tier}`}
                    style={({ pressed }) => ({ marginTop: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 44, borderRadius: 999, backgroundColor: isNext ? c.brand : c.surface, borderWidth: isNext ? 0 : 1.5, borderColor: c.brand, opacity: pressed || (t.tier === 3 && tier3Busy) ? 0.8 : 1 })}
                  >
                    <ZIcon name="shield" size={17} color={isNext ? '#fff' : c.brand} />
                    <Text style={{ fontSize: 14, fontFamily: font.bold, color: isNext ? '#fff' : c.brand }}>{t.tier === 3 && tier3Busy ? 'Checking…' : `Verify Tier ${t.tier}`}</Text>
                  </Pressable>
                )}
              </View>
            </View>
          );
        })}
      </Card>

      <Sheet open={tier3 !== null} onClose={() => setTier3(null)}>
        <View style={{ alignItems: 'center', paddingTop: 4 }}>
          <View style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: tier3?.kind === 'done' ? 'rgba(0,160,26,0.12)' : tier3?.kind === 'progress' ? 'rgba(217,119,6,0.10)' : 'rgba(232,163,61,0.14)', alignItems: 'center', justifyContent: 'center' }}>
            {tier3?.kind === 'progress' ? <ZIcon name="history" size={34} color={AMBER} stroke={1.8} /> : <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: tier3?.kind === 'done' ? '#00A01A' : '#E8A33D', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={tier3?.kind === 'done' ? 'check' : 'help'} size={24} color="#fff" stroke={3} />
            </View>}
          </View>
          <Text style={{ fontSize: 21, fontFamily: font.extrabold, color: c.ink1, marginTop: 18, textAlign: 'center' }}>
            {tier3?.kind === 'done' ? 'You\u2019re now Tier 3' : tier3?.kind === 'progress' ? 'Tier 3 is in progress' : 'Address checks aren\u2019t open yet'}
          </Text>
          <Text style={{ fontSize: 14, fontFamily: font.regular, color: c.ink3, marginTop: 8, lineHeight: 21, textAlign: 'center', paddingHorizontal: 4 }}>
            {tier3?.kind === 'done'
              ? 'Your address check had already come through, so your new limits apply right away.'
              : tier3?.kind === 'progress'
                ? 'We\u2019re checking the address you gave when you signed up. We\u2019ll let you know when it\u2019s done. There\u2019s nothing else to do.'
                : tier3?.message || 'We can\u2019t check addresses with the bank yet. Your address is saved, and we\u2019ll let you know when Tier 3 opens.'}
          </Text>
          {tier3?.kind === 'done' && tier3Limits ? (
            <View style={{ alignSelf: 'stretch', flexDirection: 'row', marginTop: 18, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: tint }}>
              {[['Per transfer', tier3Limits.per_transfer], ['Daily', tier3Limits.daily_transfer]].map(([label, value]) => (
                <View key={label} style={{ flex: 1 }}>
                  <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>{label}</Text>
                  <NText style={{ fontSize: 16, marginTop: 2, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{amountLabel(value)}</NText>
                </View>
              ))}
            </View>
          ) : null}
          {status.address ? (
            <View style={{ alignSelf: 'stretch', flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: tier3?.kind === 'done' && tier3Limits ? 10 : 18, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: c.surface2 }}>
              <ZIcon name="home" size={18} color={c.ink2} />
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>Address</Text>
                <Text style={{ fontSize: 13.5, marginTop: 2, fontFamily: font.bold, color: c.ink1 }}>{status.address}</Text>
              </View>
              {tier3?.kind === 'unavailable' ? null : (
                <View style={{ paddingVertical: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: tier3?.kind === 'done' ? 'rgba(0,160,26,0.10)' : 'rgba(217,119,6,0.10)' }}>
                  <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: tier3?.kind === 'done' ? '#15803D' : AMBER }}>{tier3?.kind === 'done' ? 'Verified' : 'Checking'}</Text>
                </View>
              )}
            </View>
          ) : null}
          <View style={{ alignSelf: 'stretch', marginTop: 20 }}>
            <Btn label={tier3?.kind === 'done' ? 'Done' : 'OK, got it'} onPress={() => { setTier3(null); void loadAccount(); }} />
          </View>
        </View>
      </Sheet>
      </>}
    </Screen>
  );
};

export default AccountLimits;
