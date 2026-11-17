import React, { useRef, useState } from 'react';
import { Animated, Platform, RefreshControl, ScrollView, View, type NativeScrollEvent, type NativeSyntheticEvent, type ScrollViewProps } from 'react-native';
import { ZCoil } from '@/components/design/Loading';

export type CoilRefreshProps = ScrollViewProps & {
  refreshing: boolean;
  onRefresh: () => void;
  children: React.ReactNode;
};

/** How far the content is pulled, in points, before the coil is fully in. */
export const COIL_FULL = 72;

/**
 * Pull to refresh with the Zitch coil (17 Nov 2026), iOS and web.
 *
 * iOS keeps the system's own pull: the scroll view bounces, UIRefreshControl
 * decides when it has been pulled far enough and holds the gap while it loads.
 * Only its spinner is hidden; the coil sits in that gap instead, fading and
 * growing in with the pull. Scrolling is exactly the system's.
 *
 * Android has its own file (CoilRefresh.android.tsx): its scroll view does not
 * bounce, so the pull is a gesture beside the scroll. The web has no pull.
 */
export const CoilRefreshScrollView = ({ refreshing, onRefresh, children, onScroll, ...scroll }: CoilRefreshProps) => {
  const [offset] = useState(() => new Animated.Value(0));
  const [pulling, setPulling] = useState(false);
  const pullingNow = useRef(false);

  if (Platform.OS === 'web') {
    return <ScrollView {...scroll} onScroll={onScroll}>{children}</ScrollView>;
  }

  const track = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const pulled = e.nativeEvent.contentOffset.y < -2;
    if (pulled !== pullingNow.current) { pullingNow.current = pulled; setPulling(pulled); }
    onScroll?.(e);
  };
  const opacity = offset.interpolate({ inputRange: [-COIL_FULL + 16, -16, 0], outputRange: [1, 0.2, 0], extrapolate: 'clamp' });
  const scale = offset.interpolate({ inputRange: [-COIL_FULL - 8, -16, 0], outputRange: [1, 0.6, 0.6], extrapolate: 'clamp' });

  return (
    <View style={{ flex: 1 }}>
      {/* Behind the content: it shows in the gap the pull opens above it. */}
      {pulling || refreshing ? (
        <Animated.View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
          style={{ position: 'absolute', top: 12, left: 0, right: 0, alignItems: 'center', opacity, transform: [{ scale }] }}>
          <ZCoil height={46} box={38} />
        </Animated.View>
      ) : null}
      <Animated.ScrollView
        {...scroll}
        scrollEventThrottle={16}
        onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: offset } } }], { useNativeDriver: true, listener: track })}
        refreshControl={
          // The system's pull and timing, without its spinner: the coil is the indicator.
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="transparent"
            colors={['transparent']} progressBackgroundColor="transparent" accessibilityLabel={refreshing ? 'Refreshing' : undefined} />
        }
      >
        {children}
      </Animated.ScrollView>
    </View>
  );
};

export default CoilRefreshScrollView;
