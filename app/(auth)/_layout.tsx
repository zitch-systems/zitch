import React from 'react';
import { Stack } from 'expo-router';
import { useTheme } from '@/lib/theme';

const AuthLayout = () => {
  const { c } = useTheme();
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.bg } }}>
      <Stack.Screen name="signin" />
      <Stack.Screen name="register" />
      <Stack.Screen name="otp" />
      <Stack.Screen name="setpassword" />
      <Stack.Screen name="setpin" />
      <Stack.Screen name="setup" />
      <Stack.Screen name="setthumbprint" />
      <Stack.Screen name="completed" />
      <Stack.Screen name="securitysetup" />
      <Stack.Screen name="accountdetails" />
    </Stack>
  );
};

export default AuthLayout;
