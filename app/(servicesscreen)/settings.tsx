import { Redirect } from 'expo-router';

/**
 * Settings now lives on the Me dashboard — preferences, security, about and the
 * WhatsApp link are sections there rather than a separate screen. Two copies of
 * the same toggles drifted apart (each had its own layout and its own idea of
 * which rows existed), so this route stays only to keep existing links and any
 * deep link working, and sends them to the one screen that owns them.
 */
export default function Settings() {
  return <Redirect href="/me" />;
}
