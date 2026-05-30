import { StyleSheet, Text, View, Image } from 'react-native'
import React from 'react'
import { Stack, Tabs } from 'expo-router'
import { images } from "../../constants";

const Navicon = ({ icon, color, name, focused })  => {
    return (
      <View>
        <Image
        source ={icon}
        
        />
      </View>
    )
    
    }

const Homelayout = () => {
    return (
        <>
        <Tabs>
<Tabs.Screen 
name='home'
options={{
  title: 'home',
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
  title: 'wallet',
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
  title: 'loan',
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
        </>
      )
}

export default Homelayout

const styles = StyleSheet.create({})