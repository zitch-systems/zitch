import { Text, View, Image, Alert, ActivityIndicator } from 'react-native';
import React, { useEffect, useState } from 'react';
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
const SetPassword = () => {
  
  const [isUpdating, setIsUpdating] = useState(false);
  const [eightcharacters, setEightCharacters] = useState(false);
  const [hasAlphabet, setHasAlphabet] = useState(false);
  const [hasNumber, setHasNumber] = useState(false);
  const [passTally, setPassTally] = useState(false);
  const [memoryEmail, setMemoryEmail] = useState('');

  const [passwordform, setPasswordForm] = useState({
    password1: '',
    password2: ''
  });

  // Check if password has at least 8 characters
  useEffect(() => {
    const passkey1 = passwordform.password1;
    const passkey2 = passwordform.password2;
    setEightCharacters(passkey1.length >= 8);
    setHasAlphabet(/[A-Za-z]/.test(passkey1));
    setHasNumber(/[0-9]/.test(passkey1));
    setPassTally(passkey1 === passkey2);

    const loadUserData = async () => {
      try {
        const grabemail = await AsyncStorage.getItem('UserEmail');
        if (grabemail) setMemoryEmail(grabemail);
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    }
    loadUserData();
  }, [passwordform.password1, passwordform.password2]);

  // Handle password update logic
  const HandlePasswordUpdate = async () => {
    // Your password update logic here
    setIsUpdating(true);
    if (!passTally) {
      Alert.alert("Error", "Password And Confirm Password Does Not Match");
      setIsUpdating(false);
      return;
    }
    if (passwordform.password1 === '') {
      Alert.alert("Error", "Password field cannot be empty!");
      setIsUpdating(false);
      return;
    }
    try {
      const response = await fetch(`${baseUrl}/api/set-password/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email: memoryEmail,
          password: passwordform.password1,
        }),
      });

      const result = await response.json();
      if (response.ok) {
        router.push("/setpin")
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
                <Text className="text-[#00101A] font-psemibold px-3">Setup Password</Text>
                <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">Setup your account password</Text>
                
                <FieldForm 
                  title="Password"
                  value={passwordform.password1}
                  handleChangeText={(e) => setPasswordForm({ ...passwordform, password1: e })}
                  otherStyles="mt-4 "
                  keyboardType="default"
                  secureTextEntry={true}
                  placeholder="Password"
                />
                
                <FieldForm 
                  title="Password"
                  value={passwordform.password2}
                  handleChangeText={(e) => setPasswordForm({ ...passwordform, password2: e })}
                  otherStyles="mt-7 mb-5 "
                  keyboardType="default"
                  secureTextEntry={true}
                  placeholder="Confirm Password"
                />
                {/* Define password rules */}
                <Text>
                  {renderButtonTitle(eightcharacters ? images.tick : images.untick, "Must be at least 8 characters")}
                </Text>

                <Text>
                  {renderButtonTitle(hasAlphabet ? images.tick : images.untick, "Must include an alphabet (Aa-Zz)")}
                </Text>
                
                <Text>
                  {renderButtonTitle(hasNumber ? images.tick : images.untick, "Must include a number (0-9)")}
                </Text>

                {/* Continue buttons */}
                <ContinueButton
                  title="Continue"  
                  handlePress={HandlePasswordUpdate}
                  containerStyling="bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
                  textStyling="text-white font-psemibold text-lg px-3"
                  isLoading={isUpdating}
                />
                {isUpdating && <ActivityIndicator size="large" color="#009b8f" />}
                <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">By clicking Continue, you agree to our <Link className="text-[#009b8f]" href="http://facebook.com">Privacy Policy and Terms and Conditions</Link> </Text>
              </View>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default SetPassword;
