import { Text, View, Image, Alert, ActivityIndicator, TextInput } from 'react-native';
import React, { useEffect, useState } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { images } from "../../constants";
import ContinueButton from '@/components/CustomButtons/CustomButton';
import { Link, router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';

const Completed = () => {
  const [isUpdating, setIsUpdating] = useState(false);







  return (
    <LinearGradient
      colors={['#44B9B0', '#FFFFFF']}
      start={{ x: 5, y: 1 }}
      end={{ x: 1, y: 2 }}
      style={{ flex: 1 }}
    >
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaView className="h-full">
          <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'flex-start', alignItems: 'center', paddingTop: 20 }}>
            <View className="w-full justify-center px-4 my-6">
              <Image 
                source={images.logo1}
                resizeMode='contain'
                className="w-[115px] h-[35px] self-center mt-10 mb-20"
              />
              
              <View className="min-h-[50vh] justify-center ">
              <Image 
                                source={images.complete}
                                resizeMode='contain'
                                className="w-[115px] h-[35px] "
                            />
                <Text className="text-[#00101A] font-psemibold text-lg px-3">Setup Complete</Text>
                <Text className="text-[#8B8B8B] mt-2 pl-2 mb-60">Congratulations! Your account setup has been successfully completed.</Text>
                
              
                
                {/* Continue buttons */}
                <ContinueButton
                  title="Proceed Home"  
                  handlePress={()=> router.push("/home")}
                  containerStyling="bg-[#FAFBFF]  w-[330px] min-h-[50px] justify-center items-center mt-5"
                  textStyling="text-black items-center"
                  isLoading={isUpdating}
                />
                
                {isUpdating && <ActivityIndicator size="large" color="#009b8f" />}
              
              </View>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default Completed;
