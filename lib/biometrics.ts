import * as LocalAuthentication from 'expo-local-authentication';
import AsyncStorage from '@react-native-async-storage/async-storage';

const ENABLED_KEY = 'z-biometrics';

/** Whether the device has biometric hardware that's enrolled at the OS level. */
export async function isBiometricAvailable(): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const isEnrolled = await LocalAuthentication.isEnrolledAsync();
  return hasHardware && isEnrolled;
}

/** The supported biometric kind, used to label the UI (Face ID vs fingerprint). */
export async function biometricLabel(): Promise<'face' | 'fingerprint' | 'biometrics'> {
  try {
    const types = await LocalAuthentication.supportedAuthenticationTypesAsync();
    if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) return 'face';
    if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) return 'fingerprint';
  } catch {
    // fall through
  }
  return 'biometrics';
}

/** Prompts the OS biometric sheet. Resolves true only on a successful scan. */
export async function authenticate(prompt = 'Authenticate'): Promise<boolean> {
  try {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: prompt,
      fallbackLabel: 'Use PIN',
      disableDeviceFallback: false,
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
}
