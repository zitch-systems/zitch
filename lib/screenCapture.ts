import { useEffect, useId } from 'react';
import { Platform } from 'react-native';
import * as ScreenCapture from 'expo-screen-capture';

/**
 * Keep PIN entry out of screenshots and screen recordings without blocking the
 * rest of the app. Receipt JPEG export uses an in-app view capture, so a global
 * screen-capture ban also prevented the app from creating its own receipt file.
 */
export const usePinScreenProtection = (active = true): void => {
  // Expo tracks active protections by key. A unique key per mounted PIN surface
  // means closing one sheet cannot re-enable capture while another PIN surface
  // is still visible (for example during a route/sheet transition).
  const protectionKey = `zitch-pin-${useId()}`;
  useEffect(() => {
    if (!active || Platform.OS === 'web') return;

    ScreenCapture.preventScreenCaptureAsync(protectionKey).catch(() => {});
    return () => {
      ScreenCapture.allowScreenCaptureAsync(protectionKey).catch(() => {});
    };
  }, [active, protectionKey]);
};
