import { Text, View, Image, Alert, ActivityIndicator } from 'react-native';
import React, { useEffect, useState } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Picker } from '@react-native-picker/picker';
import { images } from "../../constants";
import ContinueButton from '@/components/CustomButtons/CustomButton';
import baseUrl from '@/components/configFiles/apiConfig';
import SelectForm from '@/components/CustomField/selectfield';
import { getToken } from '@/lib/secureStore';

// Nigerian electricity distribution companies. The numeric values follow the
// same convention the backend uses for network/cable selection.
// TODO: confirm the disco id mapping and the request field names with the API.
const DISCOS = [
  { label: 'Ikeja Electric (IKEDC)', value: '1' },
  { label: 'Eko Electric (EKEDC)', value: '2' },
  { label: 'Abuja Electric (AEDC)', value: '3' },
  { label: 'Kano Electric (KEDCO)', value: '4' },
  { label: 'Port Harcourt Electric (PHED)', value: '5' },
  { label: 'Jos Electric (JED)', value: '6' },
  { label: 'Kaduna Electric (KAEDCO)', value: '7' },
  { label: 'Enugu Electric (EEDC)', value: '8' },
  { label: 'Ibadan Electric (IBEDC)', value: '9' },
  { label: 'Benin Electric (BEDC)', value: '10' },
];

const BuyElectricity = () => {
  const [isBuying, setIsBuying] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [token, setToken] = useState('');
  const [customerName, setCustomerName] = useState('');

  const [form, setForm] = useState({
    disco: '',
    meterType: '',
    meter: '',
    amount: '',
    transactionPin: '',
  });

  useEffect(() => {
    const loadUserData = async () => {
      try {
        const access_token = await getToken();
        if (access_token) setToken(access_token);
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    };
    loadUserData();
  }, []);

  // Reset a previously resolved customer when the meter inputs change.
  useEffect(() => {
    setCustomerName('');
  }, [form.disco, form.meterType, form.meter]);

  const validateAmount = () => {
    const amount = parseFloat(form.amount);
    if (isNaN(amount)) {
      Alert.alert('Error', 'Please enter a valid amount.');
      return null;
    }
    if (amount < 100) {
      Alert.alert('Error', 'Minimum amount is ₦100.');
      return null;
    }
    return amount;
  };

  const handleValidateMeter = async () => {
    if (form.disco === '') {
      Alert.alert('Error', 'Please select a disco.');
      return;
    }
    if (form.meterType === '') {
      Alert.alert('Error', 'Please select Prepaid or Postpaid.');
      return;
    }
    if (form.meter.trim() === '') {
      Alert.alert('Error', 'Please enter your meter number.');
      return;
    }

    setIsValidating(true);
    try {
      const response = await fetch(`${baseUrl}/api/utility/validate_meter/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          meter: form.meter,
          disco: form.disco,
          meter_type: form.meterType,
        }),
      });

      const result = await response.json();
      if (response.ok) {
        setCustomerName(result.customer_name || result.name || 'Verified');
      } else {
        Alert.alert('Error', result.message || 'Could not verify meter number.');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsValidating(false);
    }
  };

  const handleBuyElectricity = async () => {
    if (form.disco === '') {
      Alert.alert('Error', 'Please select a disco.');
      return;
    }
    if (form.meterType === '') {
      Alert.alert('Error', 'Please select Prepaid or Postpaid.');
      return;
    }
    if (form.meter.trim() === '') {
      Alert.alert('Error', 'Please enter your meter number.');
      return;
    }
    const amount = validateAmount();
    if (amount === null) return;
    if (form.transactionPin.trim() === '') {
      Alert.alert('Error', 'Please enter your transaction PIN.');
      return;
    }

    setIsBuying(true);
    try {
      const response = await fetch(`${baseUrl}/api/utility/buyelectricity/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          disco: form.disco,
          meter: form.meter,
          meter_type: form.meterType,
          amount: form.amount,
          access_token: token,
          transaction_pin: form.transactionPin,
        }),
      });

      const result = await response.json();
      if (response.ok) {
        if (result.token) setToken(result.token);
        Alert.alert(
          'Success',
          result.token
            ? `Purchase successful. Token: ${result.token}`
            : result.message || 'Transaction Successful'
        );
      } else {
        Alert.alert('Error', result.message || 'Transaction Failed');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsBuying(false);
    }
  };

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
            <View className="w-full justify-center px-2 my-3">
              <View className="min-h-[85vh] justify-center p-3 rounded-lg shadow-lg">
                <Text className="text-[#00101A] font-semibold px-3 text-center text-xl mb-6">Pay Electricity Bill</Text>
                <Image
                  source={images.electric}
                  resizeMode='contain'
                  className="w-[150px] h-[35px] self-center mt-5 mb-4"
                />

                <View className="mt-4 mb-2">
                  <Picker
                    selectedValue={form.disco}
                    onValueChange={(itemValue) => setForm({ ...form, disco: itemValue })}
                    style={{ height: 50, width: '100%', borderWidth: 1, borderColor: '#000', borderRadius: 10 }}
                  >
                    <Picker.Item label="Select Disco" value="" />
                    {DISCOS.map((d) => (
                      <Picker.Item key={d.value} label={d.label} value={d.value} />
                    ))}
                  </Picker>
                </View>

                <View className="mt-4 mb-2">
                  <Picker
                    selectedValue={form.meterType}
                    onValueChange={(itemValue) => setForm({ ...form, meterType: itemValue })}
                    style={{ height: 50, width: '100%', borderWidth: 1, borderColor: '#000', borderRadius: 10 }}
                  >
                    <Picker.Item label="Select Meter Type" value="" />
                    <Picker.Item label="Prepaid" value="prepaid" />
                    <Picker.Item label="Postpaid" value="postpaid" />
                  </Picker>
                </View>

                <SelectForm
                  title="Meter Number"
                  value={form.meter}
                  handleChangeText={(e) => setForm({ ...form, meter: e })}
                  otherStyles="mt-2 mb-1"
                  keyboardType="numeric"
                  placeholder="Enter Meter Number"
                />
                {customerName ? (
                  <Text className="text-[#009b8f] mb-2 px-1">✓ {customerName}</Text>
                ) : null}
                <ContinueButton
                  title="Validate Meter"
                  handlePress={handleValidateMeter}
                  containerStyling="bg-white border border-[#009b8f] rounded-xl w-full min-h-[40px] justify-center items-center mt-1 mb-2"
                  textStyling="text-[#009b8f] font-semibold px-3"
                  isLoading={isValidating}
                />

                <SelectForm
                  title="Amount"
                  value={form.amount}
                  handleChangeText={(e) => setForm({ ...form, amount: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="numeric"
                  placeholder="Enter Amount"
                />

                <SelectForm
                  title="Transaction PIN"
                  value={form.transactionPin}
                  handleChangeText={(e) => setForm({ ...form, transactionPin: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="numeric"
                  placeholder="Enter Transaction PIN"
                  secureTextEntry={true}
                />

                <ContinueButton
                  title="Pay Now"
                  handlePress={handleBuyElectricity}
                  containerStyling="bg-[#009b8f] rounded-xl w-full min-h-[50px] justify-center items-center mt-2"
                  textStyling="text-white font-semibold text-lg px-3"
                  isLoading={isBuying}
                />
                {(isBuying || isValidating) && (
                  <ActivityIndicator size="large" color="#009b8f" style={{ marginTop: 10 }} />
                )}
              </View>
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default BuyElectricity;
