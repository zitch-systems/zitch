import { Text, View, Image, Alert, ActivityIndicator } from 'react-native';
import React, { useEffect, useState } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Picker } from '@react-native-picker/picker'; // Add Picker for dropdown
import { images } from "../../constants";
import FieldForm from '@/components/CustomField/fieldForm'; // Correct the import path
import ContinueButton from '@/components/CustomButtons/CustomButton';
import { Link, router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import SelectForm from '@/components/CustomField/selectfield';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
const BuyAirtime = () => {
  const [isBuying, setisBuying] = useState(false);
  const [memoryEmail, setMemoryEmail] = useState('');

  const [airtimeForm, setairtimeForm] = useState({
    network: '',
    amount: '',
    phone: '',
    transaction_pin: '',
  });

  useEffect(() => {
    const loadUserData = async () => {
      try {
        const access_token = await getToken();
        if (access_token) setMemoryEmail(access_token);
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    }
    loadUserData();
  }, []);

  const stripHtmlTags = (str) => {
    return str.replace(/<\/?[^>]+(>|$)/g, "");
  };

  const HandleBuyAirtime = async () => {
    setisBuying(true);

    if (airtimeForm.network === '') {
      Alert.alert("Error", "Network field cannot be empty!");
      setisBuying(false);
      return;
    }

    let amount = stripHtmlTags(airtimeForm.amount);
    amount = parseFloat(amount);

    if (isNaN(amount)) {
      Alert.alert("Error", "Please enter a valid amount.");
      setisBuying(false);
      return;
    }

    if (amount < 0) {
      Alert.alert("Error", "Amount cannot be negative.");
      setisBuying(false);
      return;
    }

    if (amount < 100) {
      Alert.alert("Error", "Minimum recharge amount is ₦100.");
      setisBuying(false);
      return;
    }

    try {
      const response = await fetch(`${baseUrl}/api/utility/buyairtime/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          access_token: memoryEmail,
          network: airtimeForm.network,
          phone: airtimeForm.phone,
          amount: airtimeForm.amount,
          transaction_pin: airtimeForm.transaction_pin
        }),
      });

      const result = await response.json();
      if (response.ok) {
        Alert.alert('Success', result.message || "Transaction Successful");
      } else {
        Alert.alert('Error', result.message || 'Incorrect Details');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setisBuying(false);
    }
  };

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
          <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', alignItems: 'center', paddingTop: 10 }}>
            <View className="w-full justify-center px-4 my-3">
              <View className="min-h-[85vh] justify-center bg-white p-3 rounded-lg shadow-lg">
                <Text className="text-[#00101A] font-semibold px-3 text-center text-xl mb-6">Buy Airtime</Text>
                <Image 
                source={images.netprovider}
                resizeMode='contain'
                className="w-[150px] h-[35px] self-center mt-5"
              />
                <View className="mt-4 mb-6">
  <Text className="mb-2 text-lg text-[#00101A]">Network</Text>
  <Picker
    selectedValue={airtimeForm.network}
    onValueChange={(itemValue) => setairtimeForm({ ...airtimeForm, network: itemValue })}
    className="w-full border border-gray-300 rounded-lg  text-base"
    style={{ height: 50, width: '100%', borderWidth: 1, borderColor: '#000', borderRadius: 10 }}
  >
    <Picker.Item label="Select Network" value="" />
    <Picker.Item label="MTN" value="1" />
    <Picker.Item label="GLO" value="2" />
    <Picker.Item label="Airtel" value="3" />
    <Picker.Item label="9Mobile" value="4" />
  </Picker>
</View>
                
                <SelectForm 
                  title="Amount"
                  value={airtimeForm.amount}
                  handleChangeText={(e) => setairtimeForm({ ...airtimeForm, amount: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="numeric"
                  placeholder="Enter Amount"
                />

                <SelectForm 
                  title="Phone Number"
                  value={airtimeForm.phone}
                  handleChangeText={(e) => setairtimeForm({ ...airtimeForm, phone: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="phone-pad"
                  placeholder="Enter Phone Number"
                />
                
                <SelectForm
                  title="Transaction PIN"
                  value={airtimeForm.transaction_pin}
                  handleChangeText={(e) => setairtimeForm({ ...airtimeForm, transaction_pin: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="numeric"
                  placeholder="Enter Transaction PIN"
                  secureTextEntry={true}
                />
                
                <ContinueButton
                  title="Buy Airtime"
                  handlePress={HandleBuyAirtime}
                  containerStyling="bg-[#009b8f] rounded-xl w-[300x] min-h-[50px] justify-center items-center mt-3"
                  textStyling="text-white font-semibold text-lg px-3"
                  isLoading={isBuying}
                />
                {isBuying && <ActivityIndicator size="large" color="#009b8f" style={{ marginTop: 10 }} />}
                
                
              </View>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default BuyAirtime;