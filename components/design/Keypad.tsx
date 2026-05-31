import React from 'react';
import { View, Text, Pressable } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

// Numeric keypad shared by the OTP and Set-PIN screens (3×4 grid).
export const Keypad = ({ onKey }: { onKey: (k: string) => void }) => {
  const { c } = useTheme();
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
      {keys.map((k, i) => (
        <View key={i} style={{ width: '33.33%', padding: 4 }}>
          {k === '' ? (
            <View style={{ height: 58 }} />
          ) : (
            <Pressable
              onPress={() => onKey(k)}
              style={({ pressed }) => ({
                height: 58,
                borderRadius: 14,
                backgroundColor: c.surface,
                alignItems: 'center',
                justifyContent: 'center',
                opacity: pressed ? 0.7 : 1,
              })}
            >
              {k === 'del' ? (
                <ZIcon name="left" size={22} color={c.ink2} />
              ) : (
                <Text style={{ fontSize: 25, fontFamily: font.semibold, color: c.ink1 }}>{k}</Text>
              )}
            </Pressable>
          )}
        </View>
      ))}
    </View>
  );
};

export default Keypad;
