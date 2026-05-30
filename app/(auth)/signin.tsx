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
const Signin = () => {

    const [ischecking, setIsChecking] = useState(false);
    const [islog, setIsLog] = useState(true);
    const [form, setForm] = useState({
        email: '',
        password: '',
    });

    //set session timeout
    const setSessionTimeout = () => {
        const sessionDuration = 3600000; // 1 hour in milliseconds
        setTimeout(async () => {
            await AsyncStorage.removeItem('userID');
            await AsyncStorage.removeItem('sessionExpiration');
            await AsyncStorage.removeItem('access_token');
            Alert.alert('Session expired', 'You have been logged out due to inactivity.');
            router.push('/signin') // Navigate back to Register screen
        }, sessionDuration);
    };

    const handleSignin = async () => {
        setIsChecking(true);
        if(form.email.trim() ===''){
            Alert.alert("Error", "Email cannot be null");
            setIsChecking(false);
            return
      
          }
          if(form.password.trim() === ''){
            Alert.alert("Error", "Password  cannot be null");
            setIsChecking(false);
            return
          }
        try {
            const response = await fetch(`${baseUrl}/api/sigin/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    email_or_phone: form.email,
                    password: form.password,
                }),
            });

            const result = await response.json();
            if (response.ok) {
                // Alert.alert('Success', 'You have successfully signed in!');
                router.push("/home")
                const access_token = result.access_token
                await AsyncStorage.setItem('access_token', access_token);
                await AsyncStorage.setItem('userID', form.email);
                // await AsyncStorage.setItem('isUserLoggedIn', islog);

                await AsyncStorage.setItem('sessionExpiration', Date.now().toString()); // Save session start time
                setSessionTimeout(); // Set session timeout
                // You can navigate to another screen here
            } else {
                Alert.alert('Error', result.message || 'Incorrect Details');
            }
        } catch (error) {
            Alert.alert('Error', 'Something went wrong. Please try again later.');
        } finally {
            setIsChecking(false);
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
                        <View className="w-full justify-center px-4 my-6">
                            <Image 
                                source={images.logo1}
                                resizeMode='contain'
                                className="w-[115px] h-[35px] self-center mt-20"
                            />
                            <Text className="text-2xl text-semibold ml-20 mt-8 pt-50 pb-5 justify-center items-center font-psemibold" >
                                Access Account
                            </Text>
                            <FieldForm 
                                title="Email"
                                value={form.email}
                                handleChangeText={(e) => setForm({ ...form, email: e })}
                                otherStyles="mt-7"
                                keyboardType="email-address"
                                placeholder="Email Address"
                            />
                            <FieldForm 
                                title="Password"
                                value={form.password}
                                handleChangeText={(e) => setForm({ ...form, password: e })}
                                otherStyles="mt-7"
                                keyboardType="default"
                                secureTextEntry={true}
                                placeholder="Password"
                            />
                            <ContinueButton
                                title="Sign In"  
                                handlePress={handleSignin}
                                containerStyling="bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
                                textStyling="text-white font-psemibold text-lg px-3"
                                isLoading={ischecking}
                            />
                            {ischecking && <ActivityIndicator size="large" color="#009b8f" />}
                            <Text style={{ paddingLeft:35, paddingTop:5, color: 'black', fontStyle: 'italic', marginTop: 10 }}>You Don't Have An Account? <Link href="/register" className="justify-center items-center" style={{ color: '#00ead8', textDecorationLine: 'underline' }}>Create One</Link></Text>
                        </View>
                    </ScrollView>
                </SafeAreaView>
            </GestureHandlerRootView>
        </LinearGradient>
    );
};

export default Signin;
