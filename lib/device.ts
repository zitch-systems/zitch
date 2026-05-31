import { useWindowDimensions } from 'react-native';

export type DeviceClass = 'phone' | 'fold' | 'tablet';

/**
 * Classifies the screen by width, matching the handoff's three device classes.
 * Wide screens (fold/tablet) get a left sidebar instead of the bottom nav.
 */
export function useDeviceClass(): DeviceClass {
  const { width } = useWindowDimensions();
  if (width >= 900) return 'tablet';
  if (width >= 600) return 'fold';
  return 'phone';
}

export function useIsWide(): boolean {
  return useDeviceClass() !== 'phone';
}
