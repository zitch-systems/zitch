import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { Btn, PinSheet, Sheet } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { takeBiometricPayOffer, type BioKind } from '@/lib/payApproval';
import { TRANSACTION_PIN_LENGTH } from '@/lib/transactionPin';

/**
 * On a payment receipt (18 Nov 2026): after a payment approved with the typed
 * PIN, offer to approve the next ones with the fingerprint (or Face ID). Shown
 * at most once every five days, and never when it is already on
 * (lib/payApproval decides). Turning it on asks for the PIN once, as Security
 * Center's switch does.
 */
export default function BioPayOffer({ show }: { show: boolean }) {
  const { c } = useTheme();
  const [kind, setKind] = useState<BioKind | null>(null);
  const [offerOpen, setOfferOpen] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pinError, setPinError] = useState('');
  const mounted = useRef(true);
  const asked = useRef(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => () => {
    mounted.current = false;
    timers.current.forEach(clearTimeout);
  }, []);

  useEffect(() => {
    if (!show || asked.current) return;
    asked.current = true;
    // A beat after the receipt lands, so the receipt is seen first.
    timers.current.push(setTimeout(() => {
      takeBiometricPayOffer()
        .then((k) => { if (mounted.current && k) { setKind(k); setOfferOpen(true); } })
        .catch(() => { /* an offer must never get in the way of a receipt */ });
    }, 700));
  }, [show]);

  if (!kind) return null;
  const name = kind === 'face' ? 'Face ID' : 'fingerprint';
  const Name = kind === 'face' ? 'Face ID' : 'Fingerprint';
  const inSentence = kind === 'face' ? 'Face ID' : 'your fingerprint';

  const turnOn = () => {
    setOfferOpen(false);
    setPinError('');
    timers.current.push(setTimeout(() => { if (mounted.current) setPinOpen(true); }, 320));
  };

  const enable = async (pin: string) => {
    if (busy) return;
    setBusy(true);
    setPinError('');
    try {
      // Loaded when used: a receipt should not pull in the network and keychain just to draw.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { enableBiometricPay } = require('@/lib/bioPayEnable') as typeof import('@/lib/bioPayEnable');
      const result = await enableBiometricPay(pin);
      if (!mounted.current) return;
      if (result.ok) {
        setPinOpen(false);
        notify('All set', `${Name} payments are on.`);
      } else {
        setPinError(result.message);
      }
    } catch {
      if (mounted.current) setPinError(`${Name} payments were not turned on. Please try again.`);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <>
      <Sheet open={offerOpen} onClose={() => setOfferOpen(false)} title="Pay faster next time">
        <View style={{ alignItems: 'center', paddingTop: 4, paddingBottom: 18 }}>
          <View style={{ width: 84, height: 84, borderRadius: 42, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name={kind === 'face' ? 'faceid' : 'fingerprint'} size={44} color={c.brand} />
          </View>
          <Text style={{ marginTop: 16, fontSize: 17, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center' }}>
            {`Approve payments with ${inSentence}`}
          </Text>
          <Text style={{ marginTop: 8, fontSize: 14, lineHeight: 21, fontFamily: font.regular, color: c.ink3, textAlign: 'center', paddingHorizontal: 8 }}>
            A quick scan instead of typing your PIN. Your PIN still works whenever you need it.
          </Text>
        </View>
        <Btn label={`Turn on ${name}`} icon={kind === 'face' ? 'faceid' : 'fingerprint'} onPress={turnOn} />
        <View style={{ height: 10 }} />
        <Btn label="Not now" variant="outline" onPress={() => setOfferOpen(false)} />
      </Sheet>
      <PinSheet
        open={pinOpen}
        onClose={() => { if (!busy) setPinOpen(false); }}
        onComplete={enable}
        busy={busy}
        error={pinError}
        title={`Turn on ${name}`}
        subtitle={`Enter your ${TRANSACTION_PIN_LENGTH}-digit PIN once to approve payments with ${inSentence}.`}
      />
    </>
  );
}
