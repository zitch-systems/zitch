import React, { useState } from "react";
import { Text, View, Image, Modal, TouchableOpacity, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { ScrollView } from "react-native-gesture-handler";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { images } from "../../constants";
import FieldForm from "@/components/CustomField/fieldForm";
import ContinueButton from "@/components/CustomButtons/CustomButton";
import { Link, router } from "expo-router";
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from "@/components/configFiles/apiConfig";
const Register = () => {
  const [isRegistering, setIsRegistering] = useState(false);
  const [registrationForm, setRegistrationForm] = useState({
    email: "",
    phone: "",
  });
  const [alertVisible, setAlertVisible] = useState(false);
  const [alertMessage, setAlertMessage] = useState("");

  const handleSignup = async () => {
    // Validate input fields
    if (registrationForm.email.trim() === '') {
      setAlertMessage("Email cannot be empty");
      setAlertVisible(true);
      return;
    }
    if (registrationForm.phone.trim() === '') {
      setAlertMessage("Phone cannot be empty");
      setAlertVisible(true);
      return;
    }

    setIsRegistering(true);
    
    try {
      const response = await fetch(`${baseUrl}/api/phone_verification/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            email: registrationForm.email,
            phone: registrationForm.phone,
          }),
        }
      );

      const result = await response.json();
      if (response.ok) {
        setAlertMessage("You have successfully registered!");
        setAlertVisible(true);
        await AsyncStorage.setItem('UserEmail', registrationForm.email);
        await AsyncStorage.setItem('UserPhone', registrationForm.phone);
        router.push("/otp");
      } else {
        setAlertMessage(result.message || "Failed to register an account");
        setAlertVisible(true);
      }
    } catch (error) {
      setAlertMessage("Something went wrong. Please try again later.");
      setAlertVisible(true);
    } finally {
      setIsRegistering(false);
    }
  };

  return (
    <LinearGradient
      colors={["#44B9B0", "#FFFFFF"]}
      start={{ x: 4, y: 1 }}
      end={{ x: 0, y: 1 }}
      style={{ flex: 1 }}
    >
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaView className="h-full">
          <ScrollView
            contentContainerStyle={{
              flexGrow: 1,
              justifyContent: "flex-start",
              alignItems: "center",
              paddingTop: 20,
            }}
          >
            <View className="w-full justify-center min-h-[85vh] px-4 my-6">
              <Image
                source={images.logo1}
                resizeMode="contain"
                className="w-[115px] h-[35px] self-center"
              />
              <Text className="text-2xl text-semibold mt-10 font-psemibold">
                Create Account
              </Text>
              <FieldForm
                title="Phone"
                value={registrationForm.phone}
                handleChangeText={(e) => setRegistrationForm({ ...registrationForm, phone: e })}
                otherStyles="mt-7"
                placeholder="+234"
              />
              <FieldForm
                title="Email"
                value={registrationForm.email}
                handleChangeText={(e) => setRegistrationForm({ ...registrationForm, email: e })}
                otherStyles="mt-4"
                placeholder="zitch@gmail.com"
              />
              <ContinueButton
                title="Sign Up"
                handlePress={handleSignup}
                containerStyling="bg-[#009b8f] rounded-xl w-[340px] min-h-[50px] justify-center items-center mt-5"
                textStyling="text-white font-psemibold text-lg px-3"
                isLoading={isRegistering}
              />
              {isRegistering && <ActivityIndicator size="large" color="#009b8f" />}
              <Text
                style={{
                  paddingLeft: 35,
                  paddingTop: 5,
                  color: "black",
                  fontStyle: "italic",
                  marginTop: 10,
                }}
              >
                Already Have An Account?{" "}
                <Link
                  href="/signin"
                  className="justify-center items-center"
                  style={{ color: "#00ead8", textDecorationLine: "underline" }}
                >
                  Login Now{" "}
                </Link>
              </Text>
            </View>
          </ScrollView>
          <Modal
            animationType="slide"
            transparent={true}
            visible={alertVisible}
            onRequestClose={() => {
              setAlertVisible(!alertVisible);
            }}
          >
            <View style={styles.centeredView}>
              <View style={styles.modalView}>
                <Text style={styles.modalText}>{alertMessage}</Text>
                <TouchableOpacity
                  style={styles.closeButton}
                  onPress={() => setAlertVisible(!alertVisible)}
                >
                  <Text style={styles.textStyle}>Close</Text>
                </TouchableOpacity>
              </View>
            </View>
          </Modal>
        </SafeAreaView>
      </GestureHandlerRootView>
    </LinearGradient>
  );
};

const styles = {
  centeredView: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    marginTop: 22,
  },
  modalView: {
    margin: 20,
    backgroundColor: "white",
    borderRadius: 20,
    padding: 35,
    alignItems: "center",
    shadowColor: "#000",
    shadowOffset: {
      width: 0,
      height: 2,
    },
    shadowOpacity: 0.25,
    shadowRadius: 4,
    elevation: 5,
  },
  closeButton: {
    backgroundColor: "#009b8f",
    borderRadius: 20,
    padding: 10,
    elevation: 2,
  },
  textStyle: {
    color: "white",
    fontWeight: "bold",
    textAlign: "center",
  },
  modalText: {
    marginBottom: 15,
    textAlign: "center",
  },
};

export default Register;
