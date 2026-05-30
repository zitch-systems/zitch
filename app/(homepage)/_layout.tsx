import { StyleSheet, Text, View, Image } from 'react-native'
import React from 'react'
import { Stack, Tabs } from 'expo-router'
import { images } from "../../constants";
import AuthGuard from "@/components/AuthGuard";

const Navicon = ({ icon, color, name, focused })  => {
    return (
      <View>
        <Image
        source ={icon}
        resizeMode="contain"
        style={{ width: 24, height: 24, tintColor: color }}
        />
      </View>
    )

    }

const Homelayout = () => {
    return (
        <AuthGuard>
        <Tabs>
<Tabs.Screen 
name='home'
options={{
  title: 'Home',
  headerShown: false,
  tabBarIcon: ({color, focused}) =>(
    <Navicon 
    icon ={images.homeicon}
    color = {color}
    name = 'Home'
    focused={focused}
    />
  )

}}
/>


{/* wallet */}
<Tabs.Screen 
name='wallet'
options={{
  title: 'Wallet',
  headerShown: false,
  tabBarIcon: ({color, focused}) =>(
    <Navicon 
    icon ={images.walleticon}
    color = {color}
    name = 'wallet'
    focused={focused}
    />
  )
  
}}
/>

<Tabs.Screen 
name='loan'
options={{
  title: 'Loan',
  headerShown: false,
  tabBarIcon: ({color, focused}) =>(
    <Navicon 
    icon ={images.loanicon}
    color = {color}
    name = 'loan'
    focused={focused}
    />
  )
  
}}
/>

<Tabs.Screen 
name='profile'
options={{
  title: 'Profile',
  headerShown: false,
  tabBarIcon: ({color, focused}) =>(
    <Navicon 
    icon ={images.profileicon}
    color = {color}
    name = 'Profile'
    focused={focused}
    />
  )
  
}}
/>
   </Tabs>
        </AuthGuard>
      )
}

export default Homelayout

const styles = StyleSheet.create({})