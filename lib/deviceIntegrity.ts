import * as Device from 'expo-device';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * Device identity and integrity signals, sent to the backend so it can bind a
 * session to a device and score risk (see `backend/common/risk.py`).
 *
 * Three deliberate boundaries:
 *
 * 1. **It is an install id, not a hardware id.** Android's ANDROID_ID and iOS's
 *    identifierForVendor are app-scoped anyway, and a hardware identifier would be a
 *    tracking identifier we have no need for and would have to declare. A random
 *    UUID kept in the keychain identifies *this install on this device*, which is
 *    exactly what "was this the device you usually use?" needs.
 *
 * 2. **The signals are advisory, never a client-side gate.** Nothing here decides
 *    whether money moves. Every value is attacker-controlled — a rooted device can
 *    return whatever it likes — so the backend treats them as evidence to score, not
 *    as a verdict to trust. Blocking a rooted device in the client would only teach
 *    an attacker to flip one boolean, while locking out honest users who root their
 *    own phones.
 *
 * 3. **No PII.** Model, OS version and brand are coarse hardware facts. The device
 *    NAME is deliberately excluded: `Device.deviceName` is routinely a person's real
 *    name ("Vivian's iPhone").
 */

const DEVICE_ID_KEY = 'z-device-id';
const isWeb = Platform.OS === 'web';

// Same accessibility class as the session token: not synced to iCloud, not restored
// onto another handset. A device id that survived a backup restore would be worse
// than none, because it would vouch for a device the user has never used.
const KEYCHAIN_OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export type DeviceSignals = {
  id: string;
  /** 'android' | 'ios' | 'web' */
  os: string;
  osVersion: string;
  model: string;
  brand: string;
  /** True when running on real hardware; false in an emulator/simulator. */
  physical: boolean;
  /** Root/jailbreak indication. `null` when the check could not run. */
  rooted: boolean | null;
};

let cachedId: string | null | undefined;

function randomId(): string {
  // No crypto dependency: this is a correlation handle, not a secret. Collisions
  // would merely merge two devices' history, and ~30 random base-36 chars makes that
  // vanishingly unlikely.
  //
  // Padded to a fixed 40 chars rather than concatenating raw `Math.random()` output:
  // base-36 conversion drops trailing zeros, so the naive version yields a
  // variable-length id — a poor property for a key that gets indexed and eyeballed.
  let out = Date.now().toString(36);
  while (out.length < 40) out += Math.random().toString(36).slice(2).padEnd(11, '0');
  return out.slice(0, 40);
}

/** A stable per-install id, created on first call and kept in the keychain. */
export async function getDeviceId(): Promise<string> {
  if (cachedId) return cachedId;
  if (isWeb) {
    // Web is preview-only and has no keychain; a per-session id is enough and is
    // marked as such so the backend never treats web as a bound device.
    cachedId = `web-${randomId()}`;
    return cachedId;
  }
  try {
    const existing = await SecureStore.getItemAsync(DEVICE_ID_KEY);
    if (existing) {
      cachedId = existing;
      return existing;
    }
    const created = randomId();
    await SecureStore.setItemAsync(DEVICE_ID_KEY, created, KEYCHAIN_OPTS);
    cachedId = created;
    return created;
  } catch {
    // A keychain failure must not break the request. An ephemeral id degrades risk
    // scoring for this process only; failing the call would take the whole app down
    // over telemetry.
    cachedId = `eph-${randomId()}`;
    return cachedId;
  }
}

let cachedRooted: boolean | null | undefined;

async function isRooted(): Promise<boolean | null> {
  if (cachedRooted !== undefined) return cachedRooted;
  if (isWeb) {
    cachedRooted = null;
    return null;
  }
  try {
    cachedRooted = await Device.isRootedExperimentalAsync();
  } catch {
    // `null` is not `false`: "we could not check" and "the device is clean" must
    // score differently, and a check that throws on a tampered OS is itself a signal.
    cachedRooted = null;
  }
  return cachedRooted;
}

export async function getDeviceSignals(): Promise<DeviceSignals> {
  return {
    id: await getDeviceId(),
    os: Platform.OS,
    osVersion: Device.osVersion ?? '',
    model: Device.modelName ?? '',
    brand: Device.brand ?? '',
    physical: Device.isDevice,
    rooted: await isRooted(),
  };
}

/**
 * Headers stamped on API calls. Split in two on purpose: the id is a stable
 * correlation handle that the backend indexes, while the rest is a compact
 * descriptor it parses opportunistically. Sending them as headers rather than in the
 * body means no existing request shape changes and no endpoint needs to opt in.
 */
export async function deviceHeaders(): Promise<Record<string, string>> {
  try {
    const s = await getDeviceSignals();
    return {
      'X-Zitch-Device': s.id,
      // Compact and ordered so it stays readable in a log line and cheap to parse.
      // `rooted` is omitted rather than guessed when the check could not run.
      'X-Zitch-Device-Info': [
        `os=${s.os}`,
        `osv=${s.osVersion}`,
        `model=${s.model}`,
        `brand=${s.brand}`,
        `physical=${s.physical ? 1 : 0}`,
        ...(s.rooted === null ? [] : [`rooted=${s.rooted ? 1 : 0}`]),
      ].join(';').slice(0, 200),
    };
  } catch {
    // Never let device telemetry stop a request — including a money request.
    return {};
  }
}

/** Test seam: forget the cached id and root result. */
export function __resetDeviceCache(): void {
  cachedId = undefined;
  cachedRooted = undefined;
}
