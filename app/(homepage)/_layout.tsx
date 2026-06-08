import React from 'react';
import { Tabs } from 'expo-router';
import AuthGuard from '@/components/AuthGuard';
import BottomNav from '@/components/design/BottomNav';
import Sidebar from '@/components/design/Sidebar';
import { useIsWide, useRailWidth } from '@/lib/device';

const HomeLayout = () => {
  const wide = useIsWide();
  const railW = useRailWidth();
  return (
    <AuthGuard>
      <Tabs
        // Phone: bottom nav. Fold/tablet: left sidebar RAIL beside the scene.
        // bottom-tabs (v6) always renders the custom tabBar at the bottom, so the
        // Sidebar positions itself absolutely on the left and we pad the scene by
        // the rail width here so content sits beside — not under — the rail.
        tabBar={(props) => (wide ? <Sidebar {...props} width={railW} /> : <BottomNav {...props} />)}
        sceneContainerStyle={wide ? { paddingLeft: railW } : undefined}
        screenOptions={{ headerShown: false }}
      >
        <Tabs.Screen name="home" />
        <Tabs.Screen name="wallet" />
        <Tabs.Screen name="convert" />
        <Tabs.Screen name="loan" />
        <Tabs.Screen name="cards" />
        <Tabs.Screen name="me" />
        <Tabs.Screen name="history" options={{ href: null }} />
        <Tabs.Screen name="notifications" options={{ href: null }} />
        <Tabs.Screen name="txndetail" options={{ href: null }} />
      </Tabs>
    </AuthGuard>
  );
};

export default HomeLayout;
