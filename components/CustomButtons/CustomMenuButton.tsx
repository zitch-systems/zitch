import React from 'react';
import { TouchableOpacity, Text, Image, StyleSheet } from 'react-native';

const CustomMenuButton = ({ onPress, iconSource, text, backgroundColor, textStyle }) => {
  return (
    <TouchableOpacity
      style={[styles.button, { backgroundColor }]}
      onPress={onPress}
    >
      {iconSource && <Image source={iconSource} style={styles.buttonIcon} />}
      <Text style={[styles.buttonText, textStyle]}>{text}</Text>
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 15,
    paddingHorizontal: 20,
    borderRadius: 10,
    flex: 1,
    marginHorizontal: 5,
    maxWidth: '48%', // Ensure two buttons fit in one row with some margin
    minWidth: '48%', // Ensure buttons take a consistent width
  },
  buttonIcon: {
    width: 20,
    height: 20,
    marginRight: 10,
  },
  buttonText: {
    color: '#fff',
    fontSize: 12,
    flexShrink: 1, // Allow text to shrink if it overflows
  },
});

export default CustomMenuButton;
