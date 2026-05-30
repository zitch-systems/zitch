import React, { useState, useEffect } from "react";
import {
  StyleSheet,
  Text,
  View,
  Image,
  ScrollView,
  TouchableOpacity,
  Alert,
  Linking,
} from "react-native";
import { images, icons } from "../../constants";
import CustomButton from "@/components/CustomButtons/CustomButton";
import CustomMenuButton from "@/components/CustomButtons/CustomMenuButton";
import { router } from "expo-router";
import baseUrl from "@/components/configFiles/apiConfig";
import AsyncStorage from "@react-native-async-storage/async-storage";
import UtilityButton from "@/components/CustomButtons/UtilityButton";
import AccountButton from "@/components/CustomButtons/AccountButton";
import { getToken, clearSession } from "@/lib/secureStore";
import { TERMS_URL } from "@/components/configFiles/links";

const Profile = () => {
  const [isBalanceVisible, setIsBalanceVisible] = useState(true);
  const [balance, setBalance] = useState(null);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [transactions, setTransactions] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [token, setToken] = useState(null);

  const toggleBalanceVisibility = () => {
    setIsBalanceVisible(!isBalanceVisible);
  };

  useEffect(() => {
    const fetchAccessToken = async () => {
      try {
        const token = await getToken();
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
      fetchAccountBalance();
      fetchTransactions();
    }
  }, [token, currentPage]);

  const fetchUserInfo = async () => {
    fetch(`${baseUrl}/api/wallet_balance/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          setBalance(data.wallet);
          setLastName(data.user_last_name);
          setFirstName(data.user_first_name);
          setPhone(data.user_phone_number);
          // Adjust if you want to use a different part of the response
        } else {
          console.error("Failed to fetch user info:", data);
        }
      })
      .catch((error) => {
        console.error("Error fetching user info:", error);
      });
  };

  const fetchAccountBalance = async () => {
    fetch(`${baseUrl}/api/wallet_balance/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          setBalance(data.wallet);
        } else {
          console.error("Failed to fetch account balance:", data);
        }
      })
      .catch((error) => {
        console.error("Error fetching account balance:", error);
      });
  };

  const fetchTransactions = async () => {
    fetch(`${baseUrl}/api/user-transaction-history/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.status) {
          setTransactions(data.all_site_transactions);
        } else {
          console.error("Failed to fetch transactions:", data);
        }
      })
      .catch((error) => {
        console.error("Error fetching transactions:", error);
      });
  };

 const handleLogout = async ()=>{
   await clearSession();
   router.push('/signin')
 };
 
 const HandleVisitSite = (url: string) => {
   Linking.openURL(url).catch(() =>
     Alert.alert('Error', 'Unable to open the link right now.')
   );
 };

 
  return (
    <ScrollView style={styles.container}>
      <View className="pt-5">
        <View style={styles.userInfo}>
          <Image source={images.profileavatar} style={styles.avatar} />
          <View style={styles.userDetails}>
            <Text style={styles.welcome}>
              {firstName} {lastName}
            </Text>
            <Text style={styles.username}> @{phone}</Text>
          </View>
        </View>
        <Text className="text-[#8B8B8B]">SETTINGS</Text>
        <AccountButton
          title="Account Details"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2"
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.accountdetails} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={() => router.push("/accountdetails")}
        />
         <AccountButton
          title="Security"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2 mb-10"
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.security} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={() => router.push("/securitysetup")}
        />
         <Text className="text-[#8B8B8B]">MORE</Text>
       <AccountButton
          title="Contact Us"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2"
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.contactus} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={() => router.push("/comingsoon")}
        />
         <AccountButton
          title="Terms & Conditions"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2 "
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.terms} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={() => HandleVisitSite(TERMS_URL)}
        />
         <AccountButton
          title="Visit Our Website"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2 "
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.website} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={() => HandleVisitSite("https://zitch.example")}
        />
        
         <AccountButton
          title="Logout"
          
          containerStyling="  w-[330px] min-h-[50px]  mt-2 pl-2 "
          textStyling="text-black  px-3"
          isLoading={false}
          leftIcon={images.logout} // Adjust according to your icon source
          rightIcon={images.frontarrow} // Adjust according to your icon source
          handlePress={handleLogout}
        />
      </View>
    </ScrollView>
  );

};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#e8f5fe",
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
  userInfo: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginVertical: 20,
    marginBottom: 40,
    paddingTop: 25,
  },
  avatar: {
    width: 50,
    height: 50,
    borderRadius: 25,
  },
  userDetails: {
    flex: 1,
    marginLeft: 10,
  },
  welcome: {
    fontSize: 16,
    color: "#555",
  },
  username: {
    fontSize: 12,
    fontWeight: "bold",
  },
  notificationIcon: {
    width: 30,
    height: 30,
  },
  balanceContainer: {
    backgroundColor: "#fff",
    padding: 20,
    borderRadius: 10,
    alignItems: "center",
    marginVertical: 20,
  },
  balanceRow: {
    flexDirection: "row",
    alignItems: "center",
  },
  balanceTitle: {
    fontSize: 16,
    color: "#555",
    marginRight: 10,
  },
  balance: {
    fontSize: 24,
    fontWeight: "bold",
  },
  buttonsContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginVertical: 10,
  },
  button: {
    backgroundColor: "#009688",
    flexDirection: "row",
    alignItems: "center",
    padding: 25,
    borderRadius: 10,
    flex: 1,
    marginHorizontal: 5,
  },
  buttonIcon: {
    width: 20,
    height: 20,
    marginRight: 10,
  },
  buttonIconbalance: {
    width: 20,
    height: 20,
  },
  menuTitle: {
    fontSize: 15,
    marginVertical: 10,
  },
  menuContainer: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
  },
  menuItem: {
    backgroundColor: "#006F66",
    padding: 15,
    borderRadius: 10,
    alignItems: "center",
    width: "30%",
    marginVertical: 5,
  },
  menuIcon: {
    width: 30,
    height: 30,
    marginBottom: 10,
  },
  menuText: {
    fontSize: 11,
    color: "#FFFFFF",
  },
  transactionTitle: {
    fontSize: 18,
    fontWeight: "bold",
    marginVertical: 10,
  },
  transactionContainer: {
    backgroundColor: "#fff",
    padding: 10,
    borderRadius: 10,
  },
  transaction: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginVertical: 10,
  },
  transactionText: {
    fontSize: 12,
    flex: 1,
  },
  transactionAmount: {
    fontSize: 14,
    fontWeight: "bold",
    flex: 1,
    textAlign: "right",
  },
  transactionStatus: {
    fontSize: 12,
    color: "green",
    flex: 1,
    textAlign: "right",
    marginLeft: 8,
  },
  transactionDate: {
    fontSize: 12,
    color: "#999",
    flex: 1,
    textAlign: "right",
  },
  loadMoreButton: {
    alignItems: "center",
    padding: 16,
    backgroundColor: "#009688",
    borderRadius: 4,
    margin: 16,
  },
  loadMoreText: {
    fontSize: 16,
    color: "#fff",
  },
});

export default Profile;
