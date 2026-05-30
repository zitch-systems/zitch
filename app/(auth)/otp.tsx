import { Text, View, Image, TextInput, Alert, ActivityIndicator } from 'react-native';
import React, { useState, useEffect } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { images } from "../../constants";
import ContinueButton from '@/components/CustomButtons/CustomButton';
import { Link, router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import { saveToken } from '@/lib/secureStore';

const OTPVerification = () => {
  const [isCheckingOtp, setIsCheckingOtp] = useState(false);
  const [otp, setOtp] = useState('');
  const [userEmail, setUserEmail] = useState('');
  const [userPhone, setUserPhone] = useState('');
  const [resendCount, setResendCount] = useState(0);

  useEffect(() => {
    const loadUserData = async () => {
      try {
        const email = await AsyncStorage.getItem('UserEmail');
        const phone = await AsyncStorage.getItem('UserPhone');
        if (email) setUserEmail(email);
        if (phone) setUserPhone(phone);
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    };

    loadUserData();
  }, []);

  const handleCheckOtp = async () => {
    setIsCheckingOtp(true);
    try {
      const response = await fetch(`${baseUrl}/api/verify_otp/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          otp,
          phone: userPhone,
        }),
      });

      const result = await response.json();

      if (response.ok) {
        const access_token = result.access_token;
        await saveToken(access_token);
        router.push("/setup");
      } else {
        Alert.alert('Error', result.message || 'Failed to verify OTP');
      }
    } catch (error) {
      console.error('Error in handleCheckOtp:', error);
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsCheckingOtp(false);
    }
  };

  const handleResendOtp = async () => {
    try {
      const response = await fetch(`${baseUrl}/api/resend_verify_otp/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          phone: userPhone,
        }),
      });

      const result = await response.json();

      if (response.ok) {
        setResendCount(prevCount => prevCount + 1);
        Alert.alert('Success', 'OTP has been resent');
      } else {
        Alert.alert('Error', result.message || 'Failed to resend OTP');
      }
    } catch (error) {
      console.error('Error in handleResendOtp:', error);
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    }
  };

  return (
    <LinearGradient
      colors={['#44B9B0', '#FFFFFF']}
      start={{ x: 4, y: 1 }}
      end={{ x: 0, y: 1 }}
      style={{ flex: 1 }}
    >
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaView className="h-full">
          <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'flex-start', alignItems: 'center', paddingTop: 20 }}>
            <View className="w-full justify-center min-h-[85vh] px-4 my-6">
              <Image 
                source={images.logo1}
                resizeMode='contain'
                className="w-[115px] h-[35px] self-center"
              />
              <Text className="text-2xl font-semibold mt-10 font-psemibold">
                Verify Your Account
              </Text>
              <Text className="text-base mt-5 text-center">
                Enter the OTP sent to your phone number
              </Text>
              <TextInput
                style={{
                  height: 50,
                  borderColor: '#009b8f',
                  borderWidth: 1,
                  borderRadius: 8,
                  marginTop: 20,
                  width: '100%',
                  paddingHorizontal: 10,
                  fontSize: 18,
                }}
                keyboardType="numeric"
                value={otp}
                onChangeText={setOtp}
                placeholder="Enter OTP"
                accessible={true}
                accessibilityLabel="OTP Input"
              />
              <ContinueButton
                title="Verify OTP"  
                handlePress={handleCheckOtp}
                containerStyling="bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
                textStyling="text-white font-psemibold text-lg px-3"
                isLoading={isCheckingOtp}
              />
              {isCheckingOtp && <ActivityIndicator size="large" color="#009b8f" />}
              <Text style={{ paddingLeft: 35, paddingTop: 5, color: 'black', fontStyle: 'italic', marginTop: 10 }}>
                Didn't receive an OTP? <Text onPress={handleResendOtp} className="justify-center items-center" 
                style={{ color: '#00ead8', textDecorationLine: 'underline' }}>Resend OTP</Text>
              </Text>
              <Text style={{ marginTop: 10, color: 'gray', fontSize: 12 }}>
                Resend attempts: {resendCount}
              </Text>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default OTPVerification;
