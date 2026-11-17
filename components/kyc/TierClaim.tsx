import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { Btn, NText, Sheet, Tap, money } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { kycService, type TierClaim } from '@/lib/services/kyc';
import type { LadderTier } from '@/lib/tierLadder';

/** The amber of the "still checking" sheet (design, Nov 2026). */
export const AMBER = '#B45309';
const RED = '#B42318';

export type Outcome = { tier: 2 | 3; kind: 'done' | 'progress' | 'review' };

const amountLabel = (value: unknown) => (typeof value === 'string' || typeof value === 'number')
  && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0
  ? money(Number(value)) : 'Not confirmed';

/** Where "Review details" takes the customer: the page that sends that tier's details again. */
export const reviewRoute = (tier: 2 | 3, from = 'limits') =>
  tier === 2 ? `/tier2?from=${from}&recheck=1` : `/tier3?from=${from}&review=1`;

/**
 * The customer taps Tier 2 or Tier 3 (Account limits or the identity screen).
 *
 * Zitch's own limiter (17 Nov 2026): sign-up sends the details to the bank in
 * the background and shows nothing. The account stays on its tier until this
 * tap, however soon the bank answers. The tap asks the server where it stands:
 * - confirmed: the tier applies now (a "You're now Tier N" sheet);
 * - still with the bank: a "Still checking" sheet;
 * - refused by the bank: the "Details need review" pop-up, with a way to fix them;
 * - nothing sent, or no answer in time: the page to enter the details.
 */
export function useTierClaim({ ladder, address, onChanged, onClosed, from = 'limits' }: {
  ladder: LadderTier[]; address?: string; from?: string;
  /** The tap's answer, to update the screen. */
  onChanged?: (claim: TierClaim) => void;
  /** The sheet was closed (reload the screen then, not while it is open). */
  onClosed?: () => void;
}) {
  const [busy, setBusy] = useState<0 | 2 | 3>(0);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const claim = async (tier: 2 | 3) => {
    if (busy) return;
    setBusy(tier);
    try {
      const r = await kycService.claimTier(tier);
      if (r?.success) onChanged?.(r);
      if (r?.success && r.state === 'upgraded') setOutcome({ tier, kind: 'done' });
      else if (r?.success && r.state === 'processing') setOutcome({ tier, kind: 'progress' });
      else if (r?.success && r.state === 'needs_review') setOutcome({ tier, kind: 'review' });
      else if (r?.success && r.state === 'not_confirmed') router.push(tier === 2 ? `/tier2?from=${from}&recheck=1` : `/tier3?from=${from}`);
      else if (r?.success && r.state === 'needs_tier1') notify('Finish Tier 1 first', r?.message || 'Verify your phone number and email address.');
      else if (r?.success && r.state === 'needs_tier2') notify('Verify Tier 2 first', r?.message || 'Tier 3 opens after Tier 2.');
      else notify('Could not check', r?.message || 'Please try again.');
    } catch {
      notify('Could not check', 'Check your connection and try again.');
    } finally {
      setBusy(0);
    }
  };

  const close = () => { setOutcome(null); onClosed?.(); };
  const sheets = outcome === null ? null
    : <TierOutcomeSheet outcome={outcome} ladder={ladder} address={address} from={from} onClose={close} />;

  return { claim, busy, sheets };
}

/**
 * The sheet a tap on a tier ends in: "You're now Tier N", "Still checking", or
 * the "Details need review" pop-up (also shown on its own, DetailsReviewPrompt).
 */
export const TierOutcomeSheet = ({ outcome, ladder, address, from = 'limits', onClose, onReview }: {
  outcome: Outcome; ladder: LadderTier[]; address?: string; from?: string; onClose: () => void;
  /** Review details was tapped (before the page opens). */
  onReview?: () => void;
}) => {
  const { c, theme } = useTheme();
  const tint = theme === 'dark' ? 'rgba(234,251,247,0.07)' : 'rgba(6,110,102,0.06)';
  const limits = ladder.find((t) => t.tier === outcome.tier);
  const review = outcome.kind === 'review';
  const done = outcome.kind === 'done';

  const title = done ? `You\u2019re now Tier ${outcome.tier}`
    : review ? 'Your details need review'
    : outcome.tier === 2 ? 'Still checking your details' : 'Still checking your address';
  const body = done ? 'Your details are confirmed. Your new limits apply now.'
    : review ? (outcome.tier === 2
      ? 'Your bank couldn\u2019t confirm your NIN or face check. Check your NIN and take the face check again to move up to Tier 2.'
      : 'Your bank couldn\u2019t verify your home address. Check it and send it again to move up to Tier 3.')
    : outcome.tier === 2
      ? 'Your bank is still checking your NIN and face check. Try again in a little while. You don\u2019t need to send anything.'
      : 'Your bank is still checking your address. Try again in a little while. You don\u2019t need to send it again.';

  return (
    <Sheet open onClose={onClose}>
      <View style={{ alignItems: 'center', paddingTop: 4 }} accessibilityLiveRegion="polite">
        <View style={{ width: 72, height: 72, borderRadius: 36, alignItems: 'center', justifyContent: 'center',
          backgroundColor: done ? 'rgba(0,160,26,0.12)' : review ? 'rgba(180,35,24,0.09)' : 'rgba(217,119,6,0.10)' }}>
          {done ? (
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: '#00A01A', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="check" size={24} color="#fff" stroke={3} />
            </View>
          ) : review ? (
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: RED, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ color: '#fff', fontSize: 24, lineHeight: 28, fontFamily: font.extrabold }}>!</Text>
            </View>
          ) : <ZIcon name="history" size={34} color={AMBER} stroke={1.8} />}
        </View>
        <Text accessibilityRole="header" style={{ fontSize: 21, fontFamily: font.extrabold, color: c.ink1, marginTop: 18, textAlign: 'center' }}>{title}</Text>
        <Text style={{ fontSize: 14, fontFamily: font.regular, color: c.ink3, marginTop: 8, lineHeight: 21, textAlign: 'center', paddingHorizontal: 4 }}>{body}</Text>

        {done && limits ? (
          <View style={{ alignSelf: 'stretch', flexDirection: 'row', marginTop: 18, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: tint }}>
            {[['Per transfer', limits.per_transfer], ['Daily', limits.daily_transfer]].map(([label, value]) => (
              <View key={String(label)} style={{ flex: 1 }}>
                <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>{label}</Text>
                <NText style={{ fontSize: 16, marginTop: 2, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{amountLabel(value)}</NText>
              </View>
            ))}
          </View>
        ) : null}

        {/* What needs another look, so the customer knows what the button opens. */}
        {!done ? (
          <View style={{ alignSelf: 'stretch', flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 18, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: c.surface2 }}>
            <ZIcon name={outcome.tier === 2 ? 'scan' : 'home'} size={18} color={c.ink2} />
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>{outcome.tier === 2 ? 'NIN and face check' : 'Home address'}</Text>
              {outcome.tier === 3 && address ? (
                <Text numberOfLines={2} style={{ fontSize: 11.5, marginTop: 2, fontFamily: font.semibold, color: c.ink3 }}>{address}</Text>
              ) : null}
            </View>
            <View style={{ paddingVertical: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: review ? 'rgba(180,35,24,0.09)' : 'rgba(217,119,6,0.10)' }}>
              <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: review ? RED : AMBER }}>{review ? 'Needs review' : 'Checking'}</Text>
            </View>
          </View>
        ) : null}

        <View style={{ alignSelf: 'stretch', marginTop: 20 }}>
          {review ? (
            <>
              <Btn label="Review details" onPress={() => { onReview?.(); onClose(); router.push(reviewRoute(outcome.tier, from)); }} />
              <Tap onPress={onClose} accessibilityLabel="Not now" style={{ alignSelf: 'center', minHeight: 44, justifyContent: 'center', marginTop: 6 }}>
                <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>Not now</Text>
              </Tap>
            </>
          ) : (
            <Btn label={done ? 'Done' : 'OK, got it'} onPress={onClose} />
          )}
        </View>
      </View>
    </Sheet>
  );
};
