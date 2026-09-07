import { Platform } from 'react-native';
import { Contact } from 'expo-contacts';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { purchasablePhoneNumber } from '@/lib/phone';

export type ContactPickResult =
  | { status: 'picked'; phone: string }
  | { status: 'cancelled' | 'missing' | 'unsupported' };

/** Open the OS contact picker without loading the customer's entire address
 * book into the app. The root session lock is paused while the native picker is
 * visible, just like it is for the camera and photo picker. */
export async function pickContactPhone(): Promise<ContactPickResult> {
  if (Platform.OS === 'web') return { status: 'unsupported' };
  beginExternalActivity();
  try {
    const contact = await Contact.presentPicker();
    if (!contact) return { status: 'cancelled' };
    const phones = await contact.getPhones();
    for (const entry of phones) {
      const phone = purchasablePhoneNumber(entry.number);
      if (phone) return { status: 'picked', phone };
    }
    return { status: 'missing' };
  } finally {
    endExternalActivity();
  }
}
