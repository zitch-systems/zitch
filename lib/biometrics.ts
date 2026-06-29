import { Platform } from 'react-native';
import * as LocalAuthentication from 'expo-local-authentication';
import AsyncStorage from '@react-native-async-storage/async-storage';

const ENABLED_KEY = 'z-biometrics';
const CRED_KEY = 'z-bio-cred'; // web: stored WebAuthn credential id (base64url)
const isWeb = Platform.OS === 'web';

// ---------------------------------------------------------------------------
// Web (browser) biometrics via WebAuthn platform authenticator
//
// expo-local-authentication has no web backend (its web build returns "no
// hardware"), so in a browser we use the WebAuthn API, which surfaces the same
// OS biometric (Touch ID / Windows Hello / Android fingerprint). This is a
// local device gate — like the native biometric — layered over the real auth
// (the stored session token / transaction PIN), not a server-verified login.
// ---------------------------------------------------------------------------
const hasWebAuthn = () =>
  typeof window !== 'undefined' && !!(window as any).PublicKeyCredential && !!window.navigator?.credentials;

function randomBytes(n: number): Uint8Array {
  const a = new Uint8Array(n);
  (window.crypto || (window as any).msCrypto).getRandomValues(a);
  return a;
}

function bufToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64urlToBuf(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

async function webAvailable(): Promise<boolean> {
  try {
    if (!hasWebAuthn()) return false;
    return await (window as any).PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

// First call enrols a platform credential; later calls verify against it. Either
// way the OS shows the biometric prompt and we resolve true only on success.
async function webAuthenticate(): Promise<boolean> {
  try {
    if (!hasWebAuthn()) return false;
    const stored = await AsyncStorage.getItem(CRED_KEY);
    if (!stored) {
      const cred = (await navigator.credentials.create({
        publicKey: {
          challenge: randomBytes(32),
          rp: { name: 'Zitch', id: window.location.hostname },
          user: { id: randomBytes(16), name: 'zitch-user', displayName: 'Zitch user' },
          pubKeyCredParams: [
            { type: 'public-key', alg: -7 },
            { type: 'public-key', alg: -257 },
          ],
          authenticatorSelection: {
            authenticatorAttachment: 'platform',
            userVerification: 'required',
            residentKey: 'preferred',
          },
          timeout: 60000,
        },
      })) as PublicKeyCredential | null;
      if (!cred) return false;
      await AsyncStorage.setItem(CRED_KEY, bufToB64url(cred.rawId));
      return true;
    }
    const assertion = await navigator.credentials.get({
      publicKey: {
        challenge: randomBytes(32),
        allowCredentials: [{ type: 'public-key', id: b64urlToBuf(stored), transports: ['internal'] }],
        userVerification: 'required',
        timeout: 60000,
      },
    });
    return !!assertion;
  } catch {
    return false; // user cancelled or verification failed
  }
}

// ---------------------------------------------------------------------------
// Public API (platform-agnostic)
// ---------------------------------------------------------------------------

/** Whether the device has biometric hardware that's enrolled at the OS level. */
export async function isBiometricAvailable(): Promise<boolean> {
  if (isWeb) return webAvailable();
  try {
    const [hasHardware, isEnrolled] = await Promise.all([
      LocalAuthentication.hasHardwareAsync(),
      LocalAuthentication.isEnrolledAsync(),
    ]);
    return hasHardware && isEnrolled;
  } catch {
    return false;
  }
}

/** The supported biometric kind, used to label the UI (Face ID vs fingerprint). */
export async function biometricLabel(): Promise<'face' | 'fingerprint' | 'biometrics'> {
  if (isWeb) return 'biometrics';
  try {
    const types = await LocalAuthentication.supportedAuthenticationTypesAsync();
    if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) return 'face';
    if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) return 'fingerprint';
  } catch {
    // fall through
  }
  return 'biometrics';
}

/**
 * Prompts the OS biometric sheet. Resolves true only on a successful scan.
 *
 * `biometricOnly` (default false) controls whether the device passcode/pattern
 * may substitute for a fingerprint/face. For money-authorizing prompts (paying
 * with the cached PIN, large-transfer step-up) pass `true` so the device-unlock
 * secret — which a thief may have shoulder-surfed — cannot stand in for the
 * account owner's biometric; the typed transaction PIN remains the fallback.
 * Convenience flows (e.g. app unlock) can keep the passcode fallback.
 */
export async function authenticate(prompt = 'Authenticate', biometricOnly = false): Promise<boolean> {
  if (isWeb) return webAuthenticate();
  try {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: prompt,
      fallbackLabel: 'Use PIN',
      disableDeviceFallback: biometricOnly,
    });
    return result.success;
  } catch {
    return false;
  }
}

/** Whether the user has opted into biometrics inside the app. */
export async function isBiometricEnabled(): Promise<boolean> {
  return (await AsyncStorage.getItem(ENABLED_KEY)) === '1';
}

export async function setBiometricEnabled(on: boolean): Promise<void> {
  await AsyncStorage.setItem(ENABLED_KEY, on ? '1' : '0');
  // On the web, forget the platform credential when disabling so re-enabling
  // re-enrols cleanly.
  if (isWeb && !on) await AsyncStorage.removeItem(CRED_KEY);
}
