import React from 'react';
import { View, Text, Image } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import Icon from 'react-native-vector-icons/MaterialIcons';

const ComingSoon = () => {
  return (
    <LinearGradient
      colors={['#44B9B0', '#FFFFFF']}
      style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}
    >
      <SafeAreaView className="h-full justify-center items-center">
        <View className="bg-white p-5 rounded-lg shadow-lg justify-center items-center">
          <Icon name="announcement" size={50} color="#4c669f" />
          <Text className="text-2xl font-semibold text-center text-gray-800 mt-4">
            Coming Soon!
          </Text>
          <Text className="text-center text-gray-600 mt-2">
            We're working hard to bring you something amazing. Stay tuned!
          </Text>
          <Image 
            source={{ uri: 'https://your-image-url.com/coming-soon.png' }}
            className="w-64 h-64 mt-6"
            resizeMode="contain"
          />
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
};

export default ComingSoon;