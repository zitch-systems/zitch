import { Text, View, Image, Alert, ActivityIndicator } from 'react-native';
import React, { useEffect, useState } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { ScrollView } from 'react-native-gesture-handler';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Picker } from '@react-native-picker/picker';
import { images } from "../../constants";
import ContinueButton from '@/components/CustomButtons/CustomButton';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import SelectForm from '@/components/CustomField/selectfield';
const BuyCable = () => {
  const [isBuying, setIsBuying] = useState(false);
  const [memoryEmail, setMemoryEmail] = useState('');
  
  const [price, setPrice] = useState('');
  const [cablePlanOptions, setCablePlanOptions] = useState([]);

  const [dataForm, setDataForm] = useState({
    cablenetwork: '',
    iuc: '',
    selectedcablePlan: '',
    transactionPin: ''
  });

  useEffect(() => {
    const loadUserData = async () => {
      try {
        const access_token = await AsyncStorage.getItem('access_token');
        if (access_token) setMemoryEmail(access_token);
      } catch (error) {
        console.error('Error loading user data:', error);
      }
    }
    loadUserData();
  }, []);

  useEffect(() => {
    if (dataForm.cablenetwork) {
      fetchCablePlan();
    }
  }, [dataForm.cablenetwork]);

  useEffect(() => {
    if (dataForm.selectedcablePlan) {
      fetchCablePlanPrices();
    }
  }, [dataForm.selectedcablePlan]);


  const fetchCablePlan = async () => {
   // console.log("ade " +baseUrl)
    try {
      const response = await fetch(`${baseUrl}/api/utility/get_cable_plans/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          cablenetwork: dataForm.cablenetwork,
          
        }),
      });

      const result = await response.json();
      if (response.ok) {
        const options = result.cable_plans.map(plan => ({
          label: `${plan.name} (${plan.validity})`,
          value: plan.cable_plan_code,
}));
        setCablePlanOptions(options);
      } else {
        Alert.alert('Error', result.message || 'Failed to fetch data plans');
      }
    } catch (error) {
      console.error('API request error:', error);
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    }
  };



  const handleValidate = async () => {
    // console.log("ade " +baseUrl)
     try {
       const response = await fetch(`${baseUrl}/api/utility/validate_iuc/`, {
         method: 'POST',
         headers: {
           'Content-Type': 'application/json',
         },
         body: JSON.stringify({
          iuc: dataForm.iuc,
          cablenetwork: dataForm.cablenetwork,
           
         }),
       });
 
       const result = await response.json();
       if (response.ok) {
         const options = result.cable_plans.map(plan => ({
           label: `${plan.name} (${plan.validity})`,
           value: plan.cable_plan_code,
 }));
         setCablePlanOptions(options);
       } else {
         Alert.alert('Error', result.message || 'Failed to fetch data plans');
       }
     } catch (error) {
       console.error('API request error:', error);
       Alert.alert('Error', 'Something went wrong. Please try again later.');
     }
   };

  const fetchCablePlanPrices = async () => {
    console.log("prices form"+dataForm.selectedcablePlan)
    try {
      const response = await fetch(`${baseUrl}/api/utility/get_cable_plans_price/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          cable_plan_code: dataForm.selectedcablePlan,
        }),
      });

      const result = await response.json();
      if (response.ok) {
        setPrice(result.cable_plans_price || '');
        console.log(result)
      } else {
        console.log('API response:', result); // Debug statement
        Alert.alert('Error', result.message || 'Failed to fetch data plan price');
      }
    } catch (error) {
      console.error('API request error:', error);
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    }
  };



  const handleBuyCable = async () => {
    setIsBuying(true);
    console.log(dataForm.selectedcablePlan,  dataForm.cablenetwork, dataForm.iuc, dataForm.transactionPin)

    try {
      const response = await fetch(`${baseUrl}/api/utility/buycable/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          iuc: dataForm.iuc,
          cablenetwork: dataForm.cablenetwork,
          selectedcablePlan: dataForm.selectedcablePlan,
          access_token: memoryEmail,
          transaction_pin: dataForm.transactionPin ,
          
          
        }),
      });

      const result = await response.json();
      if (response.ok) {
        Alert.alert('Success', result.message || "Transaction Successful");
      } else {
        Alert.alert('Error', result.message || 'Transaction Failed');
      }
    } catch (error) {
      console.error('API request error:', error);
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsBuying(false);
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
            <View className="w-full justify-center px-2 my-3">
              <View className="min-h-[85vh] justify-center  p-3 rounded-lg shadow-lg">
                <Text className="text-[#00101A] font-semibold px-3 text-center text-xl mb-6">Pay TV Subscription</Text>
                <Image 
                  source={images.netprovider}
                  resizeMode='contain'
                  className="w-[150px] h-[35px] self-center mt-5, mb-15"
                />
                <View className="mt-10 mb-2">
                 
                  <Picker
                    selectedValue={dataForm.cablenetwork}
                    onValueChange={(itemValue) => setDataForm({ ...dataForm, cablenetwork: itemValue })}
                    className="w-full border border-gray-300 rounded-lg p-2 text-base "
                    style={{ height: 50, width: '100%', borderWidth: 1, borderColor: '#000', borderRadius: 10 }}
                  >
                    <Picker.Item label="Select Cable Provider" value="" />
                    <Picker.Item label="GoTV" value="1" />
                    <Picker.Item label="DSTV" value="2" />
                    <Picker.Item label="STARTIME" value="3" />
                    
                  </Picker>
                </View>
               
                <View className="mt-4 mb-6">
                  
                  <Picker
                    selectedValue={dataForm.selectedcablePlan}
                    onValueChange={(itemValue) => setDataForm({ ...dataForm, selectedcablePlan: itemValue })}
                    className="w-full border border-gray-300 rounded-lg p-2 text-base"
                    style={{ height: 50, width: '100%', borderWidth: 1, borderColor: '#000', borderRadius: 10 }}
                    enabled={cablePlanOptions.length > 0}
                  >
                    <Picker.Item label="Select Data Plan" value="" />
                    {cablePlanOptions.map((plan) => (
                      <Picker.Item key={plan.value} label={plan.label} value={plan.value} />
                    ))}
                  </Picker>
                  <SelectForm 
                  title="IUC"
                  value={dataForm.iuc}
                  handleChangeText={(e) => setDataForm({ ...dataForm, iuc: e })}
                  otherStyles="mt-2 mb-3"
                  keyboardType="phone-pad"
                  placeholder="IUC Number"
                />
                
                <SelectForm 
                  title="Transaction PIN"
                  value={dataForm.transactionPin}
                  handleChangeText={(e) => setDataForm({ ...dataForm, transactionPin: e })}
                  otherStyles="mt-2 "
                  keyboardType="numeric"
                  placeholder="Enter Transaction PIN"
                />
                </View>
                <View className=" mb-2">
                  <Text className="mb-2 text-lg text-[#00101A]">  {price ? `Amount: ₦${price}.00` : 'Price'}</Text>
                 
                </View>
                <ContinueButton
                  title="Validate"
                  handlePress={handleValidate}
                  containerStyling="bg-[#009b8f] rounded-xl w-[300x] min-h-[40px] justify-center items-center mt-3"
                  textStyling="text-white font-semibold text-lg px-3"
                  isLoading={isBuying}
                />
              </View>
              {isBuying && <ActivityIndicator size="large" color="#009b8f" style={{ marginTop: 10 }} />}
                
            </View>
          </ScrollView>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

export default BuyCable;
