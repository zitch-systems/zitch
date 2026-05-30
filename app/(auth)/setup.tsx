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
const Setup = () => {

   //create icon for the buttons
    const renderButtonTitle = (icon, text) => (
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Image source={icon} resizeMode='contain' style={{ width: 20, height: 20, marginRight: 8 }} />
            <Text style={{ color: 'black' }}>{text}</Text>
        </View>
    );

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
                                className="w-[115px] h-[35px] self-center mt-10"
                            />
                            <Image 
                                source={images.user}
                                resizeMode='contain'
                                className="w-[115px] h-[35px] mt-20"
                            />
                            <Text style={{ paddingTop: 5, color: '#00101A', marginTop: 0, marginBottom: 50 }}>Congratulations! Your Account has been successfully created</Text>
                            <Text className="text-[#00101A] font-psemibold px-3">Complete Setup</Text>
                            <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">Complete Your password and pin setup to secure your account</Text>
                            
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
                    </ScrollView>
                </SafeAreaView>
            </GestureHandlerRootView>
        </LinearGradient>
    );
};

export default Setup;
