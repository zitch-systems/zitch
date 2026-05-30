import { StyleSheet, Text, View, Image, TouchableOpacity } from 'react-native';
import React from 'react';
import { images } from '../../constants';

const AccountButton = ({ title, handlePress, containerStyling, textStyling, isLoading, leftIcon, rightIcon }) => {
    return (
        <View className="flex-row items-center">
            <TouchableOpacity 
                onPress={handlePress}
                activeOpacity={0.7}
                className={`${containerStyling} ${isLoading ? 'opacity-50' : ''}`} 
                disabled={isLoading}
                style={styles.button}
            >
                {leftIcon && (
                    <Image
                        source={leftIcon}
                        style={styles.leftIcon}
                        resizeMode='contain'
                    />
                )}
                <View style={styles.textContainer}>
                    <Text className={`${textStyling}`}>{title}</Text>
                   
                   
                </View>
                {rightIcon && (
                    <Image
                        source={rightIcon}
                        style={styles.rightIcon}
                        resizeMode='contain'
                    />
                )}
            </TouchableOpacity>
        </View>
    );
};

const styles = StyleSheet.create({
    button: {
        flexDirection: 'row',
        alignItems: 'center',
        paddingHorizontal: 16,
        paddingVertical: 10,
        
        
    },
    leftIcon: {
        width: 40,
        height: 40,
        marginRight: 8,
    },
    textContainer: {
        flex: 1,
    },
    description: {
        color: '#8B8B8B',
        fontSize: 12,
    },
    rightIcon: {
        width: 10,
        height: 10,
        marginLeft: 'auto',
    },
});

export default AccountButton;
