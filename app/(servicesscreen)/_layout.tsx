import { StyleSheet, Text, View } from 'react-native'
import React from 'react'
import { Stack } from 'expo-router'
const ServicesScreenLayout = () => {
  return (
    <>
    <Stack>
    <Stack.Screen name="buydata" 
    options={{ headerShown: false}}/>

<Stack.Screen name="buyairtime" 
    options={{ headerShown: false}}/>

<Stack.Screen name="buycable" 
    options={{ headerShown: false}}/>

<Stack.Screen name="utility" 
    options={{ headerShown: false}}/>


<Stack.Screen name="buyelectricity" 
    options={{ headerShown: false}}/>

    <Stack.Screen name="sendmoney" 
    options={{ headerShown: false}}/>

<Stack.Screen name="getloan" 
    options={{ headerShown: false}}/>



<Stack.Screen name="exams" 
    options={{ headerShown: false}}/>
    </Stack>
    </>
  )
}

export default ServicesScreenLayout;

const styles = StyleSheet.create({})