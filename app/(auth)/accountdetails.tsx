import { Text, View, Image, Alert, ActivityIndicator } from "react-native";
import React, { useEffect, useState } from "react";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScrollView } from "react-native-gesture-handler";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { images } from "../../constants";
import FieldForm from "@/components/CustomField/fieldForm";
import ContinueButton from "@/components/CustomButtons/CustomButton";
import AsyncStorage from "@react-native-async-storage/async-storage";
import baseUrl from "@/components/configFiles/apiConfig";
import UploadButton from "@/components/CustomButtons/UploadButton";
import { Link, router } from 'expo-router';

const AccountDetails = () => {
  const [isUpdatingRecord, setIsUpdatingRecord] = useState(false);
  const [updateUserForm, setUpdateUserForm] = useState({
    email: "",
    phone: "",
    firstName: "",
    lastName: "",
  });
  const [alertVisible, setAlertVisible] = useState(false);
  const [alertMessage, setAlertMessage] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState(null);

  useEffect(() => {
    const fetchAccessToken = async () => {
      try {
        const token = await AsyncStorage.getItem("access_token");
        setToken(token);
      } catch (error) {
        console.error("Failed to retrieve access token from storage:", error);
      }
    };

    fetchAccessToken();
  }, []);

  useEffect(() => {
    if (token) {
      fetchUserInfo();
    }
  }, [token]);

  const fetchUserInfo = async () => {
    try {
      const response = await fetch(`${baseUrl}/api/wallet_balance/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ access_token: token }),
      });
      const data = await response.json();
      if (data.success) {
        setLastName(data.user_last_name);
        setFirstName(data.user_first_name);
        setPhone(data.user_phone_number);
        setEmail(data.user_email);
      } else {
        console.error("Failed to fetch user info:", data);
      }
    } catch (error) {
      console.error("Error fetching user info:", error);
    }
  };

  const handleUpdate = async () => {
    if (updateUserForm.email.trim() === "") {
      setAlertMessage("Email cannot be empty");
      setAlertVisible(true);
      return;
    }
    if (updateUserForm.phone.trim() === "") {
      setAlertMessage("Phone cannot be empty");
      setAlertVisible(true);
      return;
    }

    setIsUpdatingRecord(true);

    try {
      const response = await fetch(`${baseUrl}/api/update_info/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: updateUserForm.email,
          phone: updateUserForm.phone,
          first_name: updateUserForm.firstName,
          last_name: updateUserForm.lastName,
          access_token: token,
        }),
      });

      const result = await response.json();
      if (response.ok) {
        setAlertMessage("Account Updated!");
        setAlertVisible(true);
        await AsyncStorage.setItem("UserEmail", updateUserForm.email);
        await AsyncStorage.setItem("UserPhone", updateUserForm.phone);
      } else {
        setAlertMessage(result.message || "Failed to Update an account");
        setAlertVisible(true);
      }
    } catch (error) {
      setAlertMessage("Something went wrong. Please try again later.");
      setAlertVisible(true);
    } finally {
      setIsUpdatingRecord(false);
    }
  };

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaView className="h-full bg-[#e8f5fe]">
        <ScrollView>
          <View className="w-full justify-center px-4 my-6">
            <Text className="text-[#00101A] font-psemibold px-3 mt-10">
              Account Details
            </Text>
            <Text className="text-[#8B8B8B] mt-2 pl-2 mb-2">
              Your Account Profile Details
            </Text>
            <View style={{ flexDirection: "row" }}>
              <Image
                source={images.profileavatar}
                className="h-50"
                resizeMode="contain"
              />
              <UploadButton
                title="Update Image"
                handlePress={handleUpdate}
                containerStyling="bg-white rounded-xl w-[180px] h-10 mt-10 ml-5 min-h-[20px] justify-center items-center"
                textStyling="text-[#009b8f] font-psemibold"
                isLoading={isUpdatingRecord}
              />
            </View>
            <View>
              <FieldForm
                title="First Name"
                value={updateUserForm.firstName}
                handleChangeText={(e) =>
                  setUpdateUserForm({ ...updateUserForm, firstName: e })
                }
                otherStyles="mt-4"
                placeholder={firstName}
              />
              <FieldForm
                title="Last Name"
                value={updateUserForm.lastName}
                handleChangeText={(e) =>
                  setUpdateUserForm({ ...updateUserForm, lastName: e })
                }
                otherStyles="mt-4"
                placeholder={lastName}
              />
              <FieldForm
                title="Email"
                value={updateUserForm.email}
                handleChangeText={(e) =>
                  setUpdateUserForm({ ...updateUserForm, email: e })
                }
                otherStyles="mt-4"
                placeholder={email}
              />
              <FieldForm
                title="Phone"
                value={updateUserForm.phone}
                handleChangeText={(e) =>
                  setUpdateUserForm({ ...updateUserForm, phone: e })
                }
                otherStyles="mt-7"
                placeholder={phone}
              />
            </View>
            <ContinueButton
              title="Update Profile"
              handlePress={handleUpdate}
              containerStyling="bg-[#009b8f] ml-15 rounded-xl w-[280px] min-h-[50px] justify-center items-center mt-5"
              textStyling="text-white font-psemibold text-lg px-3"
              isLoading={isUpdatingRecord}
            />
            {isUpdatingRecord && (
              <ActivityIndicator size="large" color="#009b8f" />
            )}
          </View>
        </ScrollView>
      </SafeAreaView>
    </GestureHandlerRootView>
  );

};

export default AccountDetails;
