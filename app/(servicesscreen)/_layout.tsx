import React from 'react';
import { Stack } from 'expo-router';
import AuthGuard from '@/components/AuthGuard';
import { useTheme } from '@/lib/theme';

const ServicesScreenLayout = () => {
  const { c } = useTheme();
  return (
    <AuthGuard>
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.bg } }}>
        <Stack.Screen name="buydata" />
        <Stack.Screen name="buyairtime" />
        <Stack.Screen name="buycable" />
        <Stack.Screen name="utility" />
        <Stack.Screen name="buyelectricity" />
        <Stack.Screen name="sendmoney" />
        <Stack.Screen name="getloan" />
        <Stack.Screen name="exams" />
        <Stack.Screen name="addmoney" />
        <Stack.Screen name="fixedsave" />
        <Stack.Screen name="savings" />
        <Stack.Screen name="betting" />
        <Stack.Screen name="support" />
        <Stack.Screen name="invite" />
        <Stack.Screen name="settings" />
        <Stack.Screen name="safetytips" />
        <Stack.Screen name="bizpayment" />
        <Stack.Screen name="junior" />
        <Stack.Screen name="bnpl" />
        <Stack.Screen name="ussd" />
        <Stack.Screen name="insurance" />
        <Stack.Screen name="remita" />
        <Stack.Screen name="movies" />
        <Stack.Screen name="water" />
        <Stack.Screen name="scan" />
        <Stack.Screen name="comingsoon" />
      </Stack>
    </AuthGuard>
  );
};

export default ServicesScreenLayout;
