/**
 * Pick a phone number from the phone's contacts, for Airtime and Data.
 *
 * Asks for the contacts permission first, every platform alike, so the customer
 * is told why before the system sheet opens; a refusal offers the Settings app.
 * Only the number the customer picks is read: nothing else from the address
 * book is kept or sent anywhere.
 */
import { Alert, Linking } from 'react-native';
import * as Contacts from 'expo-contacts';

/** "+234 803 123 4567", "2348031234567", "0803-123-4567" → "08031234567"; anything else → "". */
export const nigerianNumber = (raw: string): string => {
  let d = String(raw || '').replace(/\D/g, '');
  if (d.startsWith('234') && d.length === 13) d = `0${d.slice(3)}`;
  if (d.length === 10 && /^[789]/.test(d)) d = `0${d}`;
  return /^0[789][01]\d{8}$/.test(d) ? d : '';
};

export type PickedContact = { name: string; phone: string };

export type PickResult =
  | { ok: true; contact: PickedContact }
  | { ok: false; reason: 'denied' | 'cancelled' | 'no-number' | 'unavailable' };

export const pickContactPhone = async (): Promise<PickResult> => {
  try {
    let perm = await Contacts.getPermissionsAsync();
    if (!perm.granted && perm.canAskAgain !== false) perm = await Contacts.requestPermissionsAsync();
    if (!perm.granted) {
      Alert.alert(
        'Allow contacts',
        'To pick a number from your contacts, allow Zitch to read contacts in Settings. Zitch only reads the number you choose.',
        [{ text: 'Not now', style: 'cancel' }, { text: 'Open Settings', onPress: () => { Linking.openSettings().catch(() => {}); } }],
      );
      return { ok: false, reason: 'denied' };
    }
    // Loaded here, not at the top: the session module pulls in storage, and the
    // screens that import this file must not need it until the button is used.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { beginExternalActivity, endExternalActivity } = require('@/lib/session') as typeof import('@/lib/session');
    beginExternalActivity();
    let contact: Contacts.ExistingContact | null = null;
    try {
      contact = await Contacts.presentContactPickerAsync();
    } finally {
      endExternalActivity();
    }
    if (!contact) return { ok: false, reason: 'cancelled' };
    const phone = (contact.phoneNumbers || [])
      .map((p) => nigerianNumber(p.number || p.digits || ''))
      .find(Boolean);
    if (!phone) return { ok: false, reason: 'no-number' };
    return { ok: true, contact: { name: contact.name || '', phone } };
  } catch {
    return { ok: false, reason: 'unavailable' };
  }
};
