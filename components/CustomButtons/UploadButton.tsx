import React from 'react';
import { TouchableOpacity, Text, Image, StyleSheet } from 'react-native';

const UploadButton = ({ title, handlePress, containerStyling, textStyling, isLoading }) => {
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
  };
  
  

export default UploadButton;
