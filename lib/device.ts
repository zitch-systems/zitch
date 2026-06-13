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

/** Width of the left navigation rail on wide screens (0 on phones). Shared by
 *  the Sidebar and the Tabs scene padding so content sits beside the rail. */
export function useRailWidth(): number {
  const device = useDeviceClass();
  if (device === 'tablet') return 240;
  if (device === 'fold') return 200;
  return 0;
}
