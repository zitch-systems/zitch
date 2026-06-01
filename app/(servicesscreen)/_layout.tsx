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
        <Stack.Screen name="comingsoon" />
      </Stack>
    </AuthGuard>
  );
};

export default ServicesScreenLayout;
