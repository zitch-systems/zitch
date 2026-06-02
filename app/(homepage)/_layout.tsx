import React from 'react';
import { Tabs } from 'expo-router';
import AuthGuard from '@/components/AuthGuard';
import BottomNav from '@/components/design/BottomNav';
import Sidebar from '@/components/design/Sidebar';
import { WalletProvider } from '@/lib/wallet';
import { useIsWide } from '@/lib/device';

const HomeLayout = () => {
  const wide = useIsWide();
  return (
    <AuthGuard>
      <WalletProvider>
        <Tabs
          // Phone: bottom nav. Fold/tablet: left sidebar rail beside the scene.
          // (The custom tabBar handles placement, so no tabBarPosition needed —
          // it isn't a valid bottom-tabs option and tripped the typecheck.)
          tabBar={(props) => (wide ? <Sidebar {...props} /> : <BottomNav {...props} />)}
          screenOptions={{ headerShown: false }}
        >
          <Tabs.Screen name="home" />
          <Tabs.Screen name="wallet" />
          <Tabs.Screen name="loan" />
          <Tabs.Screen name="cards" />
          <Tabs.Screen name="me" />
          <Tabs.Screen name="history" options={{ href: null }} />
          <Tabs.Screen name="notifications" options={{ href: null }} />
          <Tabs.Screen name="txndetail" options={{ href: null }} />
        </Tabs>
      </WalletProvider>
    </AuthGuard>
  );
};

export default HomeLayout;
