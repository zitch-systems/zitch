import { StyleSheet, Text, View, TouchableOpacity } from 'react-native'
import React from 'react'

const ContinueButton = ({ title, handlePress, containerStyling, textStyling, isLoading}) => {
  return (
    <TouchableOpacity 
    onPress={handlePress}
    activeOpacity={0.7}
    
    
    className={`${containerStyling} ${isLoading ? 'opacity-50' : ''}`} 
    disabled={isLoading}
    >
<Text className={`${textStyling}`}>{title}</Text>

    </TouchableOpacity>
      
    
  )
}






export default  ContinueButton;
