import React, { useCallback, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from 'expo-router';
import { kycService } from '@/lib/services/kyc';
import { TierOutcomeSheet, type Outcome } from '@/components/kyc/TierClaim';

/** The refusals this phone has already shown, so each one pops up once. */
const SEEN_KEY = 'zitch.detailsReview.seen';
const SEEN_LIMIT = 12;

const readSeen = async (): Promise<string[]> => {
  try {
    const raw = await AsyncStorage.getItem(SEEN_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((v) => typeof v === 'string') : [];
  } catch { return []; }
};

const remember = async (key: string) => {
  if (!key) return;
  try {
    const seen = await readSeen();
    await AsyncStorage.setItem(SEEN_KEY, JSON.stringify([...seen.filter((k) => k !== key), key].slice(-SEEN_LIMIT)));
  } catch { /* it may show once more; nothing else depends on it */ }
};

/**
 * "Your details need review", on its own (17 Nov 2026). Sign-up sends Tier 2
 * and Tier 3 to the bank in the background and nothing about them is shown,
 * except this: when the bank refused the details, the customer is told the next
 * time they open Home, once per refusal. No refusal, nothing at all. The same
 * pop-up also opens when they tap that tier (useTierClaim).
 */
export const DetailsReviewPrompt = () => {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [address, setAddress] = useState('');
  const shown = useRef('');

  useFocusEffect(useCallback(() => {
    let live = true;
    void (async () => {
      try {
        const status = await kycService.getStatus();
        const rows = status?.success ? status.details_review || [] : [];
        if (!live || !rows.length) return;
        const seen = await readSeen();
        const next = rows.find((r) => (r.tier === 2 || r.tier === 3) && !seen.includes(`${r.tier}:${r.at}`));
        if (!live || !next) return;
        shown.current = `${next.tier}:${next.at}`;
        setAddress(status.address || '');
        setOutcome({ tier: next.tier, kind: 'review' });
      } catch { /* nothing to show */ }
    })();
    return () => { live = false; };
  }, []));

  if (!outcome) return null;
  const close = () => { void remember(shown.current); setOutcome(null); };
  return <TierOutcomeSheet outcome={outcome} ladder={[]} address={address} from="home" onClose={close} />;
};
