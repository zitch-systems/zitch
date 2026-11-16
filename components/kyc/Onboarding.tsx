import React, { useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Field, Btn } from '@/components/design/ui';
import { kycService } from '@/lib/services/kyc';
import { lgasFor, exactLga } from '@/lib/data/lgas';
import { useTheme, font } from '@/lib/theme';

/**
 * Sign-up's "Identity and address" page. The BVN part reuses the identity
 * screen's own verification flow; these are the pieces only sign-up shows: the
 * step titles, the SMS-or-face choice, the NIN, the face check and the address.
 */

export const NIGERIAN_STATES = [
  'Abia', 'Adamawa', 'Akwa Ibom', 'Anambra', 'Bauchi', 'Bayelsa', 'Benue', 'Borno',
  'Cross River', 'Delta', 'Ebonyi', 'Edo', 'Ekiti', 'Enugu', 'FCT', 'Gombe', 'Imo',
  'Jigawa', 'Kaduna', 'Kano', 'Katsina', 'Kebbi', 'Kogi', 'Kwara', 'Lagos', 'Nasarawa',
  'Niger', 'Ogun', 'Ondo', 'Osun', 'Oyo', 'Plateau', 'Rivers', 'Sokoto', 'Taraba',
  'Yobe', 'Zamfara',
];

const GREEN = '#15803D';

export const exactState = (typed: string) =>
  NIGERIAN_STATES.find((s) => s.toLowerCase() === typed.trim().toLowerCase()) || '';

export const StepTitle = ({ n, title, done, muted }: { n: number; title: string; done?: boolean; muted?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, opacity: muted ? 0.45 : 1 }}>
      <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: done ? '#00A01A' : c.brand, alignItems: 'center', justifyContent: 'center' }}>
        {done ? <ZIcon name="check" size={13} color="#fff" stroke={3} />
          : <Text style={{ fontSize: 12, fontFamily: font.extrabold, color: '#fff' }}>{n}</Text>}
      </View>
      <Text style={{ fontSize: 16, fontFamily: font.extrabold, color: c.ink1 }}>{title}</Text>
    </View>
  );
};

export const OnboardingHeading = ({ sub }: { sub: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ marginBottom: 18 }}>
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>Identity and address</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, fontFamily: font.regular, lineHeight: 20 }}>{sub}</Text>
    </View>
  );
};

/** One of the two ways to prove the BVN is yours. The icon sits beside the words. */
export const ViaOption = ({ on, icon, label, sub, onPress, disabled }: {
  on: boolean; icon: string; label: string; sub: string; onPress: () => void; disabled?: boolean;
}) => {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="radio"
      accessibilityState={{ selected: on, disabled }}
      accessibilityLabel={`${label}, ${sub}`}
      style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 9, padding: 10, borderRadius: 16,
        backgroundColor: on ? 'rgba(6,110,102,0.06)' : c.surface, borderWidth: on ? 2 : 1, borderColor: on ? c.brand : c.line }}
    >
      <View style={{ width: 32, height: 32, borderRadius: 16, backgroundColor: on ? c.brand : 'rgba(6,110,102,0.10)', alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={icon} size={16} color={on ? '#fff' : c.brand} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ fontSize: 14, fontFamily: font.extrabold, color: c.ink1 }}>{label}</Text>
        <Text style={{ fontSize: 11, marginTop: 1, fontFamily: font.regular, color: c.ink3, lineHeight: 14 }}>{sub}</Text>
      </View>
    </Pressable>
  );
};

/**
 * The face check as one short row (review, Nov 2026): an icon, "A quick face
 * check" and a small Start button at the end. Done, it turns into a green row
 * with Retake. `waiting` shows it greyed out until the step before is done.
 */
export const FaceCheckRow = ({ done, waiting, busy, onStart }: {
  done?: boolean; waiting?: boolean; busy?: boolean; onStart: () => void;
}) => {
  const { c } = useTheme();
  if (done) {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, paddingHorizontal: 12, borderRadius: 14,
        backgroundColor: c.surface, borderWidth: 1, borderColor: 'rgba(0,160,26,0.35)' }}>
        <View style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: 'rgba(0,160,26,0.10)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="check" size={17} color={GREEN} stroke={2.6} />
        </View>
        <Text style={{ flex: 1, fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>Face check done</Text>
        <Pressable onPress={onStart} disabled={busy} accessibilityRole="button" accessibilityLabel="Take the face check again" hitSlop={8}
          style={{ minHeight: 36, justifyContent: 'center', paddingHorizontal: 4 }}>
          <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Retake</Text>
        </Pressable>
      </View>
    );
  }
  const off = !!waiting || !!busy;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, paddingLeft: 12, paddingRight: 10, borderRadius: 16,
      backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, opacity: waiting ? 0.6 : 1 }}>
      <View style={{ width: 38, height: 38, borderRadius: 19, backgroundColor: 'rgba(6,110,102,0.08)', alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name="faceid" size={20} color={c.brand} stroke={1.8} />
      </View>
      <Text style={{ flex: 1, fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>A quick face check</Text>
      <Pressable onPress={onStart} disabled={off} accessibilityRole="button" accessibilityLabel="Start face check"
        accessibilityState={{ disabled: off }} hitSlop={6}
        style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: 36, paddingHorizontal: 14, borderRadius: 999,
          borderWidth: 1.5, borderColor: c.brand, opacity: off ? 0.5 : pressed ? 0.7 : 1 })}>
        <ZIcon name="camera" size={15} color={c.brand} />
        <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: c.brand }}>{busy ? 'Opening…' : 'Start'}</Text>
      </Pressable>
    </View>
  );
};

export const LaterLink = ({ onPress }: { onPress: () => void }) => {
  const { c } = useTheme();
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel="I'll do this later" style={{ alignItems: 'center', paddingVertical: 14 }}>
      <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>I&apos;ll do this later</Text>
    </Pressable>
  );
};

const Divider = () => {
  const { c } = useTheme();
  return <View style={{ height: 1, backgroundColor: c.line, marginVertical: 18 }} />;
};

export type AddressValue = { house: string; street: string; state: string; lga: string };
export const EMPTY_ADDRESS: AddressValue = { house: '', street: '', state: '', lga: '' };

/** The address as the server takes it, or null until every part is there (the state from the list). */
export const addressPayload = (a: AddressValue) => {
  const state = exactState(a.state);
  if (!a.house.trim() || !a.street.trim() || !state || !a.lga.trim()) return null;
  return { house_number: a.house.trim(), street: a.street.trim(), state, lga: exactLga(state, a.lga) || a.lga.trim() };
};

/** House number, street, state (from the list) and LGA (suggested, any typed value taken). */
export const AddressFields = ({ value, onChange, editable = true, onSubmit }: {
  value: AddressValue; onChange: (next: AddressValue) => void; editable?: boolean; onSubmit?: () => void;
}) => {
  const { c } = useTheme();
  const [stateOpen, setStateOpen] = useState(false);
  const [lgaOpen, setLgaOpen] = useState(false);
  const set = (patch: Partial<AddressValue>) => onChange({ ...value, ...patch });
  const matched = exactState(value.state);
  const typed = value.state.trim().toLowerCase();
  const suggestions = stateOpen && !matched
    ? NIGERIAN_STATES.filter((s) => !typed || s.toLowerCase().includes(typed)).slice(0, 6)
    : [];
  // The LGA list only suggests: a value typed that is not on it is still taken.
  const lgaMatched = exactLga(matched, value.lga);
  const lgaTyped = value.lga.trim().toLowerCase();
  const lgaSuggestions = lgaOpen && !lgaMatched
    ? lgasFor(matched).filter((l) => !lgaTyped || l.toLowerCase().includes(lgaTyped)).slice(0, 6)
    : [];
  const list = (items: string[], pick: (v: string) => void) => (
    <View accessibilityRole="list" style={{ marginTop: 6, borderRadius: 18, backgroundColor: c.surface, padding: 6, borderWidth: 1, borderColor: c.line }}>
      {items.map((item) => (
        <Pressable key={item} onPress={() => pick(item)} accessibilityRole="button" accessibilityLabel={item}
          style={{ paddingVertical: 12, paddingHorizontal: 12, borderRadius: 12 }}>
          <Text style={{ fontSize: 15, fontFamily: font.medium, color: c.ink1 }}>{item}</Text>
        </Pressable>
      ))}
    </View>
  );
  return (
    <View style={{ gap: 14 }}>
      <View style={{ flexDirection: 'row', gap: 12 }}>
        <View style={{ width: 118 }}>
          <Field label="House number" value={value.house} onChangeText={(v) => set({ house: v.slice(0, 20) })} placeholder="e.g. 12"
            keyboardType="number-pad" returnKeyType="next" editable={editable} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Field label="Street address" value={value.street} onChangeText={(v) => set({ street: v.slice(0, 120) })} placeholder="Street name and area"
            autoComplete="street-address" textContentType="streetAddressLine1" editable={editable} />
        </View>
      </View>
      <View>
        <Field
          label="State"
          value={value.state}
          onChangeText={(v) => { set({ state: v.slice(0, 40) }); setStateOpen(true); }}
          onFocus={() => setStateOpen(true)}
          onBlur={() => setTimeout(() => setStateOpen(false), 150)}
          placeholder="Select or type your state"
          autoCapitalize="words"
          editable={editable}
          suffix={matched ? <ZIcon name="check" size={18} color={GREEN} stroke={2.6} /> : <ZIcon name="down" size={18} color={c.ink3} />}
        />
        {suggestions.length > 0 && list(suggestions, (st) => { set({ state: st }); setStateOpen(false); })}
        {!!value.state.trim() && !matched && !stateOpen && (
          <Text style={{ fontSize: 12, color: c.red, marginTop: 6, marginLeft: 2, fontFamily: font.regular }}>Choose your state from the list</Text>
        )}
      </View>
      <View>
        <Field
          label="Local government area"
          value={value.lga}
          onChangeText={(v) => { set({ lga: v.slice(0, 60) }); setLgaOpen(true); }}
          onFocus={() => setLgaOpen(true)}
          onBlur={() => setTimeout(() => setLgaOpen(false), 150)}
          placeholder="Select or type your LGA"
          autoCapitalize="words"
          editable={editable}
          returnKeyType="done"
          onSubmitEditing={onSubmit}
          suffix={lgaMatched ? <ZIcon name="check" size={18} color={GREEN} stroke={2.6} /> : <ZIcon name="down" size={18} color={c.ink3} />}
        />
        {lgaSuggestions.length > 0 && list(lgaSuggestions, (l) => { set({ lga: l }); setLgaOpen(false); })}
      </View>
    </View>
  );
};

const NIN_HINT = 'Dial *346# on the phone linked to your NIN to get it.';

/** The NIN, with where to find it. */
export const NinField = ({ value, onChange, editable = true }: { value: string; onChange: (v: string) => void; editable?: boolean }) => {
  const { c } = useTheme();
  return (
    <View>
      <Field label="National Identification Number (NIN)" placeholder="11 digits" keyboardType="number-pad" editable={editable}
        value={value} onChangeText={(v) => onChange(v.replace(/\D/g, '').slice(0, 11))} prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <ZIcon name="help" size={14} color={c.amber} />
        <Text style={{ flex: 1, color: c.ink3, fontSize: 12.5, fontFamily: font.regular }}>{NIN_HINT}</Text>
      </View>
    </View>
  );
};

export type SignupFace = { image: string; token: string };

/** A refusal a new face check fixes (not a BVN/NIN problem). */
const faceRefusal = (res: { success?: boolean; pending?: boolean; code?: string; _httpStatus?: number }) =>
  !res.success && !res.pending && (/^(selfie|liveness)_/.test(res.code || '') || res._httpStatus === 502);

/**
 * Sign-up's "Identity and address" page once the BVN is verified (review, Nov
 * 2026): the NIN, a quick face check and the address. Continue sends them to
 * the bank as separate requests at the same time: the NIN and face check as the
 * Tier 2 request, the address as the Tier 3 request. The customer stays on
 * Tier 1 until they tap Verify on Account limits.
 */
export const OnboardingIdentity = ({ savedAddress, onDone, bvn, nin, onNin, face, onStartFace, onFaceRefused, faceBusy, address, onAddress }: {
  savedAddress?: string; onDone: () => void; bvn: string;
  nin: string; onNin: (v: string) => void;
  face: SignupFace | null; onStartFace: () => void; onFaceRefused: () => void; faceBusy?: boolean;
  address: AddressValue; onAddress: (v: AddressValue) => void;
}) => {
  const { c } = useTheme();
  const [bvnAgain, setBvnAgain] = useState('');
  // An address typed while the BVN was being verified is the one to send.
  const [editing, setEditing] = useState(!savedAddress || !!addressPayload(address));
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  // A request the bank already has is not sent again when the other one needs a retry.
  const sent = useRef({ tier2: false, tier3: false });

  // The bank needs the BVN typed again with the NIN (Zitch keeps only a hash).
  const bvnForBank = bvn.length === 11 ? bvn : bvnAgain;
  const identityGiven = nin.length === 11 && !!face && bvnForBank.length === 11;
  const identityStarted = nin.length > 0 || !!face;
  const payload = addressPayload(address);
  const ready = (editing ? !!payload : true) && (!identityStarted || identityGiven);

  const send = async () => {
    if (!ready || inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    try {
      // Separate requests, sent together: Tier 2 (NIN + face) and Tier 3 (address).
      const tier2 = identityGiven && face && !sent.current.tier2 ? kycService.sendTier2AtSignup(bvnForBank, nin, face.image, face.token) : null;
      const tier3 = editing && payload && !sent.current.tier3 ? kycService.sendTier3(payload) : null;
      const [r2, r3] = await Promise.allSettled([tier2, tier3]);
      const v2 = r2.status === 'fulfilled' ? r2.value : null;
      const v3 = r3.status === 'fulfilled' ? r3.value : null;
      if (tier2 && (v2?.success || (v2 as { pending?: boolean } | null)?.pending)) sent.current.tier2 = true;
      if (tier3 && v3?.success) sent.current.tier3 = true;
      if (tier3 && !v3?.success) {
        notify('Address', v3?.message || 'Could not save your address. Check your connection and try again.');
        return;
      }
      if (tier2 && !(v2?.success || (v2 as { pending?: boolean } | null)?.pending)) {
        if (v2 && faceRefusal(v2 as never)) { onFaceRefused(); notify('Face check', 'Take the face check again.'); }
        else notify('NIN and face check', v2?.message || 'Could not send them. Check your connection and try again.');
        return;
      }
      if (sent.current.tier2 || sent.current.tier3) notify('Sent for checking', 'You’re on Tier 1. Verify Tier 2 and Tier 3 in Account limits when you’re ready.', 'success');
      onDone();
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  return (
    <View>
      <OnboardingHeading sub={identityGiven || (nin.length === 11 && !!face)
        ? 'Your BVN is verified, and your NIN and face check are in. Now add your address.'
        : 'Your BVN is verified. Add your NIN, take a quick face check, then your address.'} />
      <StepTitle n={1} title="Your BVN" done />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10, marginLeft: 34 }}>
        <ZIcon name="check" size={14} color={GREEN} stroke={3} />
        <Text style={{ fontSize: 13, fontFamily: font.bold, color: GREEN }}>Verified</Text>
      </View>
      <Divider />
      <StepTitle n={2} title="Your NIN" done={nin.length === 11} />
      <View style={{ marginTop: 14, gap: 14 }}>
        {bvn.length !== 11 && identityStarted ? (
          <Field label="Bank Verification Number (BVN)" placeholder="11 digits" keyboardType="number-pad" editable={!sending}
            value={bvnAgain} onChangeText={(v) => setBvnAgain(v.replace(/\D/g, '').slice(0, 11))}
            prefix={<ZIcon name="insurance" size={18} color={c.ink3} />} />
        ) : null}
        <NinField value={nin} onChange={onNin} editable={!sending} />
      </View>
      <Divider />
      <StepTitle n={3} title="Face check" done={!!face} />
      <View style={{ marginTop: 14 }}>
        <FaceCheckRow done={!!face} busy={faceBusy || sending} onStart={onStartFace} />
      </View>
      <Divider />
      <StepTitle n={4} title="Your address" />
      {!editing ? (
        <View style={{ marginTop: 14, padding: 14, borderRadius: 16, backgroundColor: c.surface, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <ZIcon name="home" size={18} color={c.ink3} />
          <Text style={{ flex: 1, fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{savedAddress}</Text>
          <Pressable onPress={() => setEditing(true)} accessibilityRole="button" accessibilityLabel="Change address" hitSlop={8}>
            <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Change</Text>
          </Pressable>
        </View>
      ) : (
        <View style={{ marginTop: 14 }}>
          <AddressFields value={address} onChange={onAddress} editable={!sending} onSubmit={() => void send()} />
        </View>
      )}

      <View style={{ marginTop: 26 }}>
        <Btn label={sending ? 'Sending…' : 'Continue'} disabled={!ready || sending} onPress={() => void send()} />
      </View>
      {identityStarted && !identityGiven ? (
        <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 10, textAlign: 'center' }}>
          Add your NIN and take the face check, or leave both for later.
        </Text>
      ) : null}
      <LaterLink onPress={onDone} />
    </View>
  );
};

/**
 * Steps 2 to 4 under the BVN form: the NIN, the face check and the address can
 * all be done while the BVN is verified (one page, review Nov 2026). Nothing is
 * sent until Continue once the BVN is verified, because the account it all goes
 * to opens with the BVN (Tier 1).
 */
export const IdentityWaits = ({ nin, onNin, editable = true, face, onStartFace, faceBusy, address, onAddress }: {
  nin: string; onNin: (v: string) => void; editable?: boolean;
  face: SignupFace | null; onStartFace: () => void; faceBusy?: boolean;
  address: AddressValue; onAddress: (v: AddressValue) => void;
}) => (
  <View>
    <Divider />
    <StepTitle n={2} title="Your NIN" done={nin.length === 11} />
    <View style={{ marginTop: 14 }}><NinField value={nin} onChange={onNin} editable={editable} /></View>
    <Divider />
    <StepTitle n={3} title="Face check" done={!!face} />
    <View style={{ marginTop: 14 }}><FaceCheckRow done={!!face} busy={faceBusy} onStart={onStartFace} /></View>
    <Divider />
    <StepTitle n={4} title="Your address" done={!!addressPayload(address)} />
    <View style={{ marginTop: 14 }}><AddressFields value={address} onChange={onAddress} editable={editable} /></View>
  </View>
);
