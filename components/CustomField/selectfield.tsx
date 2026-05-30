import { View, TextInput, Text } from 'react-native';
import React from 'react';

const SelectForm = ({ title, value, handleChangeText, otherStyles, keyboardType, placeholder }) => {
    return (
      <View className={`w-full ${otherStyles}`}>
        <Text className="mb-2  text-[#00101A]">{title}</Text>
        <TextInput
          value={value}
          onChangeText={handleChangeText}
          keyboardType={keyboardType}
          placeholder={placeholder}
          className="w-full border border-gray-300 rounded-lg p-2 text-base"
        />
      </View>
    );
  };
export default SelectForm;