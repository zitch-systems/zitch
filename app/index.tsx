import React from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ScrollView, Text, View, Image, ImageBackground } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { Link, Redirect, router } from 'expo-router';
import {images} from '../constants'
import AsyncStorage from '@react-native-async-storage/async-storage';
import ContinueButton from '@/components/CustomButtons/CustomButton';

const Index = () => {
  return (

    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaView className="bg-[#004D47] h-full" >
        <ScrollView contentContainerStyle={{ height:' 100px' }}>
          <View className='w-full justify-center items-center min-h-[85vh] px-4 py-4'>
            {/* Add your content here */}
            <Image
            source={images.logo1}
            className = 'w-[130px] h-[84px]'
            resizeMode='contain'
            />
             <Image
            source={images.iPhone}
            className = 'max-w-[380px] w-full h-[300px]'
            resizeMode='contain'
            />
            <View className="relative mt-5">
              <Text className='text-sm text-white text-bold text-center'>The go-to app for quick Transactions, Airtime, Bills, loans & Save in $ <Text className="text-secondary-200">zitch </Text></Text>
              <Image 
           source={images.path}
           className ="w-[136px] h-[15px] absolute -bottom-2 -right-8"
           resizeMode="contained"
           
           />
           
            </View>

{/* continue wih phone or email */}
            <ContinueButton 
            title="Continue With Phone or Email"  
            handlePress={()=>router.push('/signin')}
            containerStyling= "bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
            textStyling ="text-white font-psemibold text-lg px-3"
            isLoading={false}
            />
          
          {/* continue with gmail button */}
          <ContinueButton
            title="Continue With Gmail"
            handlePress={()=>router.push('/signin')}
            containerStyling= "text-sm bg-white rounded-xl min-h-[50px] w-[340px] justify-center items-center mt-3"
            textStyling ="text-black font-psemibold text-lg px-3"
            isLoading={false}
            />

           <View>
           <Text style={{ color: 'white',fontStyle: 'italic', marginTop:10}}>
           Not A Member Yet?<Link href="/register" style={{ color: '#D6EEDA', textDecorationLine: 'underline' }}> Register An Account </Link>
           </Text>

           </View>
          
          {/* testnig */}
        
          {/* tesitng */}
            </View>
          
          
        </ScrollView>
      {/* <StatusBar backgroundColor='#161622' style='light'/> */}
      </SafeAreaView>
    </GestureHandlerRootView>
  );
};

export default Index;
