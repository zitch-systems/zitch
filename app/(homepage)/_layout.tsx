import React from 'react';
import { Tabs } from 'expo-router';
import AuthGuard from '@/components/AuthGuard';
import BottomNav from '@/components/design/BottomNav';
import { WalletProvider } from '@/lib/wallet';

const HomeLayout = () => {
  return (
    <AuthGuard>
      <WalletProvider>
        <Tabs tabBar={(props) => <BottomNav {...props} />} screenOptions={{ headerShown: false }}>
          <Tabs.Screen name="home" />
          <Tabs.Screen name="wallet" />
          <Tabs.Screen name="loan" />
          <Tabs.Screen name="cards" />
          <Tabs.Screen name="me" />
          <Tabs.Screen name="history" options={{ href: null }} />
          <Tabs.Screen name="notifications" options={{ href: null }} />
        </Tabs>
      </WalletProvider>
    </AuthGuard>
  );
};

export default HomeLayout;
