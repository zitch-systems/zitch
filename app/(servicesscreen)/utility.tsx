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
import UtilityButton from '@/components/CustomButtons/UtilityButton';

const Utility = () => {
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
                        <View className="w-full min-h-[50vh] justify-center px-4 my-6">
                            <Text className="text-[#00101A] font-psemibold px-3 pt-20">Utility Payment</Text>
                            <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">At Zitch, we provide seamless utility payments.</Text>
                            
                            <UtilityButton
                                title="Electricity"
                                description="Pay your electricity bill at your fingertips."
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                                leftIcon={images.electric} // Adjust according to your icon source
                                rightIcon={images.arrowup} // Adjust according to your icon source
                                handlePress={() => router.push("/buyelectricity")}
                            />
                            
                            <UtilityButton
                                title="TV Subscriptions"
                                description="Pay your electricity bill at your fingertips."
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                                leftIcon={images.tv} // Adjust according to your icon source
                                rightIcon={images.arrowup} // Adjust according to your icon source
                                handlePress={() => router.push("/buycable")}
                            />

<UtilityButton
                                title="Water"
                                description="Pay your electricity bill at your fingertips."
                                containerStyling="bg-white rounded-xl w-[330px] min-h-[50px] justify-center mt-2 pl-2"
                                textStyling="text-black font-psemibold text-lg px-3"
                                isLoading={false}
                                leftIcon={images.water} // Adjust according to your icon source
                                rightIcon={images.arrowup} // Adjust according to your icon source
                                handlePress={() => router.push("/comingsoon")}
                            />
                            
                           
                        </View>
                    </ScrollView>
                </SafeAreaView>
            </GestureHandlerRootView>
        </LinearGradient>
    );
};

export default Utility;
