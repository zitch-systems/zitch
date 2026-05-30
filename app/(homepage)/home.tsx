import React, { useState, useEffect } from 'react';
import { StyleSheet, Text, View, Image, ScrollView, TouchableOpacity, Alert } from 'react-native';
import { images, icons } from '../../constants';
import CustomButton from '@/components/CustomButtons/CustomButton';
import CustomMenuButton from '@/components/CustomButtons/CustomMenuButton';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken } from '@/lib/secureStore';

const Home = () => {
  const [isBalanceVisible, setIsBalanceVisible] = useState(true);
  const [balance, setBalance] = useState(null);
  const [username, setUsername] = useState('');
  const [transactions, setTransactions] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [token, setToken] = useState(null);
  const [isUserLoggedIn, setIsUserLoggedIn] = useState(false);

  const toggleBalanceVisibility = () => {
    setIsBalanceVisible(!isBalanceVisible);
  };

  useEffect(() => {
    const fetchAccessToken = async () => {
      try {
        const usetoken = await getToken();
        setToken(usetoken);
      } catch (error) {
        console.error('Failed to retrieve access token from storage:', error);
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
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          setBalance(data.wallet);
          setUsername(data.user_last_name); // Adjust if you want to use a different part of the response
        } else {
          console.error('Failed to fetch user info:', data);
        }
      })
      .catch((error) => {
        console.error('Error fetching user info:', error);
      });
  };

  const fetchAccountBalance = async () => {
    fetch(`${baseUrl}/api/wallet_balance/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.success) {
          setBalance(data.wallet);
        } else {
          console.error('Failed to fetch account balance:', data);
        }
      })
      .catch((error) => {
        console.error('Error fetching account balance:', error);
      });
  };

  const fetchTransactions = async () => {
    fetch(`${baseUrl}/api/user-transaction-history/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ access_token: token }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.status) {
          setTransactions(data.all_site_transactions);
        } else {
          console.error('Failed to fetch transactions:', data);
        }
      })
      .catch((error) => {
        console.error('Error fetching transactions:', error);
      });
  };

  const loadMoreTransactions = () => {
    setCurrentPage(currentPage + 1);
  };

  return (
    <ScrollView style={styles.container}>
      <View style={styles.userInfo}>
        <Image source={images.headicon} style={styles.avatar} />
        <View style={styles.userDetails}>
          <Text style={styles.welcome}>Welcome back 👋</Text>
          <Text style={styles.username}>@{username}</Text>
        </View>
        <Image source={images.alerticon} style={styles.notificationIcon} />
      </View>

      <View >
        <View style={styles.balanceRow}>
          <Text style={styles.balanceTitle}>Available Balance</Text>
          <TouchableOpacity onPress={toggleBalanceVisibility}>
            <Image
              source={isBalanceVisible ? icons.eye : icons.eyeHide}
              style={styles.buttonIconbalance}
            />
          </TouchableOpacity>
        </View>
        <Text style={styles.balance}>{isBalanceVisible ? `₦${balance}.00` : '*****'}</Text>
      </View>

      <View style={styles.buttonsContainer}>
        <CustomMenuButton
          backgroundColor="#009688"
          iconSource={images.depositicon}
          text="Deposit Money"
          textStyle={{ fontSize: 10 }}
          onPress={() => console.log('Deposit Money')}
        />
        <CustomMenuButton
          backgroundColor="#FAFBFF"
          iconSource={images.withdrawalicon}
          text="Withdraw Money"
          textStyle={{ fontSize: 10, color: "#004D47" }}
          onPress={() => console.log('Withdraw Money')}
        />
      </View>
      {/* first menu row */}
      <Text style={styles.menuTitle}>Select what you want to do</Text>
      <View style={styles.menuContainer}>
        <TouchableOpacity style={styles.menuItem}
        onPress={ ()=>router.push("/comingsoon")}
        >
          <Image source={images.sendmoneyicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Send money</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem} 
         onPress={ ()=>router.push("/buyairtime")}
         >
          <Image source={images.airtimeicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Buy Airtime </Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem}
        onPress={ ()=>router.push("/buydata")}>
          <Image source={images.utilityicon} style={styles.menuIcon} />
          <Text style={styles.menuText}> Buy Data</Text>
        </TouchableOpacity>
        {/* Add more menu items as needed */}
      </View>
      {/* second menu row */}
      <View style={styles.menuContainer}>
        <TouchableOpacity style={styles.menuItem}onPress={ ()=>router.push("/getloan")}>
          <Image source={images.getloanicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Get Loan</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem}
        onPress={ ()=>router.push("/comingsoon")}>
          <Image source={images.movieticketicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Movies Ticket</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/comingsoon")}>
          <Image source={images.insuranceicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Insurance</Text>
        </TouchableOpacity>
        {/* Add more menu items as needed */}
      </View>
      {/* third menu row */}
      <View style={styles.menuContainer}>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/comingsoon")}>
          <Image source={images.remitaicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Remita</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/exams")}>
          <Image source={images.jambicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Education</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/utility")}>
          <Image source={images.utilityicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Utility Bills</Text>
        </TouchableOpacity>
        {/* Add more menu items as needed */}
      </View>
      {/* fourth menu row */}
      <View style={styles.menuContainer}>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/comingsoon")}>
          <Image source={images.savemoneyicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Save Money</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem} 
        onPress={ ()=>router.push("/comingsoon")}>
          <Image source={images.convertcurrencyicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Convert Currency</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.menuItem}
        onPress={ ()=>router.push("/comingsoon")}
        >
          <Image source={images.utilityicon} style={styles.menuIcon} />
          <Text style={styles.menuText}>Others</Text>
        </TouchableOpacity>
        {/* Add more menu items as needed */}
      </View>

      
        
        <Text style={styles.transactionTitle}>Transaction History</Text>
      <View style={styles.transactionContainer}>
        {transactions.slice(0, currentPage * 5).map((transaction, index) => (
          <View key={index} style={styles.transaction}>
            <Text style={styles.transactionText}>{transaction.service}</Text>
            <Text style={styles.transactionAmount}>₦{transaction.amount}</Text>
            <Text style={styles.transactionStatus}>{transaction.transaction_status}</Text>
          </View>
        ))}
      </View>
      {transactions.length > currentPage * 5 && (
        <TouchableOpacity style={styles.loadMoreButton} onPress={loadMoreTransactions}>
          <Text style={styles.loadMoreText}>Load More</Text>
        </TouchableOpacity>
      )}
    </ScrollView>
  );}
  


const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#e8f5fe',
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
  userInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginVertical: 20,
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
    color: '#555',
  },
  username: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  notificationIcon: {
    width: 30,
    height: 30,
  },
  balanceContainer: {
    backgroundColor: '#fff',
    padding: 20,
    borderRadius: 10,
    alignItems: 'center',
    marginVertical: 20,
  },
  balanceRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  balanceTitle: {
    fontSize: 16,
    color: '#555',
    marginRight: 10,
  },
  balance: {
    fontSize: 24,
    fontWeight: 'bold',
  },
  buttonsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginVertical: 10,
  },
  button: {
    backgroundColor: '#009688',
    flexDirection: 'row',
    alignItems: 'center',
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
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    
  },
  menuItem: {
    backgroundColor: '#006F66',
    padding: 15,
    borderRadius: 10,
    alignItems: 'center',
    width: '30%',
    marginVertical: 5,
  },
  menuIcon: {
    width: 30,
    height: 30,
    marginBottom: 10,
  },
  menuText: {
    fontSize: 11,
    color: '#FFFFFF',
  },
  transactionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    marginVertical: 10,
  },
  transactionContainer: {
    backgroundColor: '#fff',
    padding: 10,
    borderRadius: 10,
  },
  transaction: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginVertical: 10,
  },
  transactionText: {
    fontSize: 12,
    flex: 1,
  },
  transactionAmount: {
    fontSize: 14,
    fontWeight: 'bold',
    flex: 1,
    textAlign: 'right',
    
  },
  transactionStatus: {
    fontSize: 12,
    color: 'green',
    flex: 1,
    textAlign: 'right',
    marginLeft:8
  },
  transactionDate: {
    fontSize: 12,
    color: '#999',
    flex: 1,
    textAlign: 'right',
  },
  loadMoreButton: {
    alignItems: 'center',
    padding: 16,
    backgroundColor: '#009688',
    borderRadius: 4,
    margin: 16,
  },
  loadMoreText: {
    fontSize: 16,
    color: '#fff',
  },
});

export default Home;
