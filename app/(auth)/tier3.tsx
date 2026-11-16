import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { Loading } from '@/components/design/Loading';
import AuthGuard from '@/components/AuthGuard';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, Card } from '@/components/design/ui';
import { AddressFields, EMPTY_ADDRESS, NIGERIAN_STATES, StepTitle, addressPayload, type AddressValue } from '@/components/kyc/Onboarding';
import { TierBadge, TierDetails, TierPill } from '@/components/design/TierRow';
import { getSessionGeneration } from '@/lib/secureStore';
import { kycService, type KycStatus } from '@/lib/services/kyc';
import { tierLadder, type LadderTier } from '@/lib/tierLadder';
import { useTheme, font } from '@/lib/theme';

type Status = KycStatus & { tier_ladder?: LadderTier[]; address?: string };

/**
 * "12 Allen Avenue, Ikeja, Lagos" (how the server keeps the sign-up address)
 * back into its parts, so the customer corrects it rather than typing it again.
 * The house number is the opening word that has a digit in it ("12", or
 * "Plot 4B"); without one the house number starts empty. Anything that does
 * not end in an LGA and a Nigerian state starts empty.
 */
export const addressParts = (saved: string): AddressValue => {
  const parts = String(saved || '').split(',').map((p) => p.trim()).filter(Boolean);
  if (parts.length < 3) return EMPTY_ADDRESS;
  const state = NIGERIAN_STATES.find((s) => s.toLowerCase() === parts[parts.length - 1].toLowerCase()) || '';
  if (!state) return EMPTY_ADDRESS;
  const lga = parts[parts.length - 2];
  const words = parts.slice(0, -2).join(', ').split(/\s+/);
  const n = /\d/.test(words[0]) ? 1 : words.length > 2 && /\d/.test(words[1]) ? 2 : 0;
  return { house: words.slice(0, n).join(' '), street: words.slice(n).join(' '), state, lga };
};

/**
 * Upgrade to Tier 3 (review, Nov 2026): reached from Verify Tier 3 when the
 * bank did not confirm the address sent at sign-up, or when none was sent. The
 * customer checks the address and sends it again, as its own Tier 3 request.
 * Tier 3 then shows Processing: Zitch does not raise Tier 3 from the bank's
 * answer until it can be tied to this address.
 */
const Tier3 = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{ from?: string }>();
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState('');
  const [address, setAddress] = useState<AddressValue>(EMPTY_ADDRESS);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const prefilled = useRef(false);
  const screenGeneration = useRef(getSessionGeneration()).current;
  const live = useCallback(() => mounted.current && screenGeneration === getSessionGeneration(), [screenGeneration]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await kycService.getStatus() as Status;
      if (!live()) return;
      if (res?.success) {
        setStatus(res);
        setLoadError('');
        if (!prefilled.current) { prefilled.current = true; setAddress(addressParts(res.address || '')); }
      } else setLoadError(res?.message || 'We could not load your verification status. Please try again.');
    } catch {
      if (live()) setLoadError('We could not load your verification status. Please try again.');
    }
  }, [live]);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const payload = addressPayload(address);

  const send = async () => {
    if (!payload || inFlight.current || !live()) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const res = await kycService.sendTier3(payload);
      if (!live()) return;
      if (res.success) {
        notify(res.tier3_state === 'not_confirmed' ? 'Not confirmed' : 'Sent for Tier 3',
          res.tier3_state === 'not_confirmed'
            ? 'Your bank could not confirm this address. Check it and send it again.'
            : 'Tier 3 shows Processing until the check is done.',
          res.tier3_state === 'not_confirmed' ? 'info' : 'success');
        if (res.tier3_state !== 'not_confirmed') {
          if (params.from === 'limits' && router.canGoBack()) router.back();
          else router.replace('/accountlimits');
        } else await load();
      } else notify('Address', res.message || 'Could not send your address. Please try again.');
    } catch {
      if (live()) notify('Error', 'Check your connection and try again.');
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const header = <Header title="Upgrade to Tier 3" sub="Check your home address and send it again" onBack={() => router.back()} />;

  if (!status) {
    return (
      <Screen>
        {header}
        {loadError ? (
          <Card>
            <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginBottom: 16 }}>{loadError}</Text>
            <Btn label="Try again" onPress={() => void load()} />
          </Card>
        ) : <Loading label="Checking your verification…" />}
      </Screen>
    );
  }

  const tier3 = tierLadder(status.tier_ladder).find((t) => t.tier === 3);
  const tier = Number(status.tier) || 0;
  const refused = status.tier3_state === 'not_confirmed';
  const processing = status.tier3_state === 'processing';
  const done = status.tier3_state === 'done' || tier >= 3;

  return (
    <Screen>
      {header}
      {tier3 ? (
        <Card>
          <View style={{ flexDirection: 'row', gap: 14 }}>
            <TierBadge tier={3} done={done} current={tier === 3} />
            <View style={{ flex: 1 }}>
              <TierDetails t={tier3} done={done} pill={<TierPill label={done ? 'Your tier' : `Tier ${tier} now`} tone={done ? 'current' : 'neutral'} />} />
            </View>
          </View>
        </Card>
      ) : null}

      {done ? (
        <Card style={{ marginTop: 14 }}><Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>You’re on Tier 3</Text></Card>
      ) : processing ? (
        <Card style={{ marginTop: 14, gap: 8 }}>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>Tier 3 is processing</Text>
          <Text style={{ fontSize: 13.5, fontFamily: font.regular, color: c.ink3, lineHeight: 20 }}>
            Your address is with the bank. Tier 3 opens once the bank’s check can be confirmed. You don’t need to send it again.
          </Text>
        </Card>
      ) : tier < 2 ? (
        <Card style={{ marginTop: 14, gap: 8 }}>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>Verify Tier 2 first</Text>
          <Text style={{ fontSize: 13.5, fontFamily: font.regular, color: c.ink3, lineHeight: 20 }}>Tier 3 opens after Tier 2.</Text>
          <Btn label="Go to Tier 2" onPress={() => router.replace('/tier2?from=limits')} />
        </Card>
      ) : (
        <>
          {refused ? (
            <View accessibilityRole="alert" style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginTop: 14, paddingVertical: 12, paddingHorizontal: 14,
              borderRadius: 16, backgroundColor: 'rgba(220,38,38,0.07)', borderWidth: 1, borderColor: 'rgba(220,38,38,0.25)' }}>
              <View style={{ marginTop: 1 }}><ZIcon name="help" size={17} color={c.red} stroke={2.2} /></View>
              <Text style={{ flex: 1, fontSize: 13, fontFamily: font.semibold, color: c.red, lineHeight: 19 }}>
                We couldn’t confirm the address from your sign-up. Check it, correct anything that’s wrong, and send it again.
              </Text>
            </View>
          ) : null}
          <Card style={{ marginTop: 14, gap: 14 }}>
            <StepTitle n={1} title="Your home address" done={!!payload} />
            <AddressFields value={address} onChange={setAddress} editable={!busy} onSubmit={() => void send()} />
            <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, lineHeight: 17 }}>
              Use the address where you live now. We check it before Tier 3 opens.
            </Text>
          </Card>
          <View style={{ marginTop: 20 }}>
            <Btn label={busy ? 'Sending…' : 'Submit for Tier 3'} disabled={!payload || busy} onPress={() => void send()} />
          </View>
          <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 10, textAlign: 'center' }}>
            Tier 3 shows Processing until the check is done.
          </Text>
        </>
      )}
    </Screen>
  );
};

const Tier3Screen = () => <AuthGuard fresh><Tier3 /></AuthGuard>;

export default Tier3Screen;
