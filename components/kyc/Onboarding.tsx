import React, { useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Field, Btn } from '@/components/design/ui';
import { kycService } from '@/lib/services/kyc';
import { useTheme, font } from '@/lib/theme';

/**
 * Sign-up's "BVN and address" page. The BVN part reuses the identity screen's
 * own verification flow; these are the pieces only sign-up shows: the step
 * titles, the SMS-or-face choice and the address form. The address is only
 * saved here. Nothing about it is checked until the customer moves to Tier 3.
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
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>BVN and address</Text>
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

/** The address step, once the BVN is verified. */
export const OnboardingAddress = ({ savedAddress, onDone }: { savedAddress?: string; onDone: () => void }) => {
  const { c } = useTheme();
  const [house, setHouse] = useState('');
  const [street, setStreet] = useState('');
  const [state, setState] = useState('');
  const [lga, setLga] = useState('');
  const [stateOpen, setStateOpen] = useState(false);
  const [editing, setEditing] = useState(!savedAddress);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);

  const matched = exactState(state);
  const typed = state.trim().toLowerCase();
  const suggestions = stateOpen && !matched
    ? NIGERIAN_STATES.filter((s) => !typed || s.toLowerCase().includes(typed)).slice(0, 6)
    : [];
  const ready = !!house.trim() && !!street.trim() && !!matched && !!lga.trim();

  const save = async () => {
    if (!ready || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    try {
      const res = await kycService.saveAddress({ house_number: house.trim(), street: street.trim(), state: matched, lga: lga.trim() });
      if (res.success) {
        notify('Address saved', undefined, 'success');
        onDone();
      } else {
        notify('Address', res.message || 'Could not save your address. Please try again.');
      }
    } catch {
      notify('Address', 'Check your connection and try again.');
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  return (
    <View>
      <OnboardingHeading sub="Your BVN is verified. Now add your address." />
      <StepTitle n={1} title="Your BVN" done />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 10, marginLeft: 34 }}>
        <ZIcon name="check" size={14} color={GREEN} stroke={3} />
        <Text style={{ fontSize: 13, fontFamily: font.bold, color: GREEN }}>Verified</Text>
      </View>
      <Divider />
      <StepTitle n={2} title="Your address" />
      <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginTop: 6, lineHeight: 18 }}>
        We just save it for now. It&apos;s used later, when you move to Tier 3.
      </Text>

      {!editing ? (
        <View style={{ marginTop: 14, padding: 14, borderRadius: 16, backgroundColor: c.surface, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <ZIcon name="home" size={18} color={c.ink3} />
          <Text style={{ flex: 1, fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{savedAddress}</Text>
          <Pressable onPress={() => setEditing(true)} accessibilityRole="button" accessibilityLabel="Change address" hitSlop={8}>
            <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Change</Text>
          </Pressable>
        </View>
      ) : (
        <View style={{ gap: 14, marginTop: 14 }}>
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <View style={{ width: 118 }}>
              <Field label="House number" value={house} onChangeText={(v) => setHouse(v.slice(0, 20))} placeholder="e.g. 12"
                keyboardType="numbers-and-punctuation" returnKeyType="next" editable={!saving} />
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Field label="Street address" value={street} onChangeText={(v) => setStreet(v.slice(0, 120))} placeholder="Street name and area"
                autoComplete="street-address" textContentType="streetAddressLine1" editable={!saving} />
            </View>
          </View>
          <View>
            <Field
              label="State"
              value={state}
              onChangeText={(v) => { setState(v.slice(0, 40)); setStateOpen(true); }}
              onFocus={() => setStateOpen(true)}
              onBlur={() => setTimeout(() => setStateOpen(false), 150)}
              placeholder="Select or type your state"
              autoCapitalize="words"
              editable={!saving}
              suffix={matched ? <ZIcon name="check" size={18} color={GREEN} stroke={2.6} /> : <ZIcon name="down" size={18} color={c.ink3} />}
            />
            {suggestions.length > 0 && (
              <View accessibilityRole="list" style={{ marginTop: 6, borderRadius: 18, backgroundColor: c.surface, padding: 6, borderWidth: 1, borderColor: c.line }}>
                {suggestions.map((s) => (
                  <Pressable key={s} onPress={() => { setState(s); setStateOpen(false); }} accessibilityRole="button" accessibilityLabel={s}
                    style={{ paddingVertical: 12, paddingHorizontal: 12, borderRadius: 12 }}>
                    <Text style={{ fontSize: 15, fontFamily: font.medium, color: c.ink1 }}>{s}</Text>
                  </Pressable>
                ))}
              </View>
            )}
            {!!state.trim() && !matched && !stateOpen && (
              <Text style={{ fontSize: 12, color: c.red, marginTop: 6, marginLeft: 2, fontFamily: font.regular }}>Choose your state from the list</Text>
            )}
          </View>
          <Field label="Local government area" value={lga} onChangeText={(v) => setLga(v.slice(0, 60))} placeholder="e.g. Ikeja"
            autoCapitalize="words" editable={!saving} returnKeyType="done" onSubmitEditing={() => void save()} />
        </View>
      )}

      <View style={{ marginTop: 26 }}>
        {editing
          ? <Btn label={saving ? 'Saving…' : 'Continue'} disabled={!ready || saving} onPress={() => void save()} />
          : <Btn label="Continue" onPress={onDone} />}
      </View>
      {editing && <LaterLink onPress={onDone} />}
    </View>
  );
};

/** Step 2 waits under the BVN form until the BVN is verified. */
export const AddressWaits = () => {
  const { c } = useTheme();
  return (
    <View>
      <Divider />
      <StepTitle n={2} title="Your address" muted />
      <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginTop: 6 }}>Opens once your BVN is verified.</Text>
    </View>
  );
};
