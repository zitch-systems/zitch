import { StyleSheet, Text, View, Image, TouchableOpacity } from 'react-native';
import React from 'react';
import { images } from '../../constants';

const UtilityButton = ({ title, description, handlePress, containerStyling, textStyling, isLoading, leftIcon, rightIcon }) => {
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
                    {description && (
                        <Text style={styles.description}>{description}</Text>
                    )}
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
        backgroundColor: 'white',
        borderRadius: 8,
    },
    leftIcon: {
        width: 24,
        height: 24,
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
        width: 24,
        height: 24,
        marginLeft: 'auto',
    },
});

export default UtilityButton;
