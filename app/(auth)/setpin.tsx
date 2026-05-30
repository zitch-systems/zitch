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
import baseUrl from '@/components/configFiles/apiConfig';
import { PRIVACY_URL } from '@/components/configFiles/links';

const SetPin = () => {
  const [isUpdating, setIsUpdating] = useState(false);
  const [transactionPin, setTransactionPin] = useState('');
  const [sixCharacters, setSixCharacters] = useState(false);
  const [memoryEmail, setMemoryEmail] = useState('');

  // Check if pin has at least 6 characters
  useEffect(() => {
    setSixCharacters(transactionPin.length >= 6);

    const loadUserData = async () => {
      try {
        const grabemail = await AsyncStorage.getItem('UserEmail');
        if (grabemail) {
          setMemoryEmail(grabemail);
          console.log('Retrieved email:', grabemail); // Debug statement
        } else {
          console.log('No email found in AsyncStorage'); // Debug statement
        }
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    };
    loadUserData();
  }, [transactionPin]);

  // Handle pin update logic
  const handlePinSet = async () => {
    setIsUpdating(true);

    if (transactionPin === '') {
      Alert.alert("Error", "Pin field cannot be empty!");
      setIsUpdating(false);
      return;
    }

    if (!sixCharacters) {
      Alert.alert("Error", "Pin field should be at least 6 digits!");
      setIsUpdating(false);
      return;
    }

    try {
      const response = await fetch(`${baseUrl}/api/set-transaction-pin/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: memoryEmail,
          pin: transactionPin,
        }),
      });

      const result = await response.json();

      if (response.ok) {
        Alert.alert('Success', 'You have successfully set the transaction pin!');
        router.push("/completed")
      } else {
        Alert.alert('Error', result.message || 'Incorrect Details');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsUpdating(false);
    }
  };

  // Create icon for the buttons
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
              <View className="min-h-[85vh] justify-center ">
                <Text className="text-[#00101A] font-psemibold px-3">Setup Pin</Text>
                <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">Setup transaction pin for your account.</Text>
                
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
                  value={transactionPin}
                  onChangeText={setTransactionPin}
                  placeholder="Enter Pin"
                  accessible={true}
                  accessibilityLabel="Pin Input"
                />
                
                {/* Continue buttons */}
                <ContinueButton
                  title="Continue"  
                  handlePress={handlePinSet}
                  containerStyling="bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
                  textStyling="text-white font-psemibold text-lg px-3"
                  isLoading={isUpdating}
                />
                
                {isUpdating && <ActivityIndicator size="large" color="#009b8f" />}
                
                <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">By clicking Continue, you agree to our <Link className="text-[#009b8f]" href={PRIVACY_URL}>Privacy Policy and Terms and Conditions</Link></Text>
              </View>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default SetPin;
