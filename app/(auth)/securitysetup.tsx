import { Text, View, Image, Alert, ActivityIndicator } from 'react-native';
import React, { useState } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { images } from "../../constants";
import FieldForm from '@/components/CustomField/fieldForm';
import ContinueButton from '@/components/CustomButtons/CustomButton';
import { Link, router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
const SecuritySetup = () => {

   //create icon for the buttons
    const renderButtonTitle = (icon, text) => (
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Image source={icon} resizeMode='contain' style={{ width: 20, height: 20, marginRight: 8 }} />
            <Text style={{ color: 'black' }}>{text}</Text>
        </View>
    );

    return (
       
            <GestureHandlerRootView style={{ flex: 1 }}>
                <SafeAreaView className="h-full bg-[#e8f5fe]">
                    <ScrollView>
                        <View className="w-full justify-center px-4 my-6">
                           
                            
                            <Text className="text-[#00101A] font-psemibold px-3 mt-10">Security</Text>
                            <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">Your Account Security Details</Text>
                           <View>
                           

                           </View>
                           <View > 
                            <ContinueButton
                                title={renderButtonTitle(images.passicon, "Password                                                            >")}
                                handlePress={()=>router.push('/setpassword')}
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                            />
                            <ContinueButton
                                title={renderButtonTitle(images.pinicon, "Transaction Pin                                                >")}
                                handlePress={()=>router.push("/setpin")}
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                            />
                            <ContinueButton
                                title={renderButtonTitle(images.thumbicon, "Thumbprint                                                        >")}
                                handlePress={()=>router.push("/comingsoon")}
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                            />
                            </View>

                        </View>
                    </ScrollView>
                </SafeAreaView>
            </GestureHandlerRootView>
        
    );
};

export default SecuritySetup;
