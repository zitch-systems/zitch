import { StyleSheet, Text, TouchableOpacity, View, Image } from 'react-native'
import React, { useState } from 'react'
import { TextInput } from 'react-native-gesture-handler';
import { icons }  from "../../constants";


const FieldForm = ({title, value, handleChangeText, otherStyles,keyboardType, placeholder,...props}) => {
        const[showPassword, setShowPassword]= useState();

    return (
    <View className={`space-y-2 ${otherStyles}` }>
      <Text className="text-base text-black-100 font-pmedium">{title}</Text>
    <View className=" border-20-black-200 w-full h-18 py-2 px-2 bg-white rounded-3xl focus:border-red items-center flex-row">
    <TextInput className="flex-1 text-black font-psemibold test-base"
    value={value}
    placeholder={placeholder}
    placeholderTextColor="black"
    onChangeText={handleChangeText}
    secureTextEntry = {title ==='Password' && !showPassword}
    />
    {title ==='Password' && (
        <TouchableOpacity onPress={() => setShowPassword(!showPassword)}>
            <Image
            source={showPassword ? icons.eye : icons.eyeHide}
            className="w-6 h-6"
            resizeMode='contain'
            />
        </TouchableOpacity>
    )}


    
    </View>
   

    </View>
  )
}

export default FieldForm;

