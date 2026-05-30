import { StyleSheet, Text, View } from 'react-native'
import React from 'react'
import { Stack } from 'expo-router'

const AuthLayout = () => {
  return (
    <>
    <Stack>
    <Stack.Screen name="signin" 
    options={{ headerShown: false}}/>

<Stack.Screen name="otp" 
    options={{ headerShown: false}}/>

<Stack.Screen name="setpassword" 
    options={{ headerShown: false}}/>

<Stack.Screen name="register" 
    options={{ headerShown: false}}/>

<Stack.Screen name="setup" 
    options={{ headerShown: false}}/>

<Stack.Screen name="setpin" 
    options={{ headerShown: false}}/>

<Stack.Screen name="securitysetup" 
    options={{ headerShown: false}}/>

    
<Stack.Screen name="accountdetails" 
    options={{ headerShown: false}}/>

<Stack.Screen name="setthumbprint" 
    options={{ headerShown: false}}/>
    <Stack.Screen name="completed" 
    options={{ headerShown: false}}/>

    </Stack>
    </>
  )
}

export default AuthLayout

const styles = StyleSheet.create({})