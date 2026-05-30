import React from 'react';
import { View, Text } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import Icon from 'react-native-vector-icons/MaterialIcons';

/**
 * Shared placeholder for features that are not built yet. Used by the
 * coming-soon route and by service screens whose flows are still stubs, so the
 * app never drops the user onto a blank/raw screen.
 */
const ComingSoonView = ({ title = 'Coming Soon!' }: { title?: string }) => {
  return (
    <LinearGradient
      colors={['#44B9B0', '#FFFFFF']}
      style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}
    >
      <SafeAreaView className="h-full justify-center items-center">
        <View className="bg-white p-8 rounded-2xl shadow-lg justify-center items-center mx-6">
          <Icon name="announcement" size={56} color="#0FA295" />
          <Text className="text-2xl font-semibold text-center text-gray-800 mt-4">
            {title}
          </Text>
          <Text className="text-center text-gray-600 mt-2">
            We're working hard to bring you something amazing. Stay tuned!
          </Text>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
};

export default ComingSoonView;
