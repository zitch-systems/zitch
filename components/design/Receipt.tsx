import React from 'react';
import { View, Text, Pressable } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

// Full-screen success receipt shown after a completed purchase.
const Receipt = ({
  title,
  message,
  rows,
  onDone,
}: {
  title: string;
  message: string;
  rows: [string, string, boolean?][];
  onDone: () => void;
}) => {
  const { c } = useTheme();
  return (
    <View style={{ flex: 1, paddingHorizontal: 22 }}>
      <View style={{ flex: 1 }}>
        <View style={{ alignItems: 'center', paddingTop: 40 }}>
          <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <View style={{ width: 78, height: 78, borderRadius: 39, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="check" size={40} color="#fff" stroke={3} />
            </View>
          </View>
          <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 22 }}>{title}</Text>
          <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, textAlign: 'center', maxWidth: 290, fontFamily: font.regular }}>{message}</Text>
        </View>

        <View style={{ marginTop: 28, borderRadius: 22, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 18, paddingVertical: 6 }}>
          {rows.map((r, i) => (
            <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 11, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
              <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{r[0]}</Text>
              <Text style={{ fontSize: r[2] ? 16 : 14, fontFamily: r[2] ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'], maxWidth: '60%', textAlign: 'right' }}>{r[1]}</Text>
            </View>
          ))}
        </View>

        <View style={{ flexDirection: 'row', gap: 10, marginTop: 16 }}>
          {[['download', 'Save'], ['share', 'Share'], ['copy', 'Copy ref']].map(([ic, lb]) => (
            <View key={ic} style={{ flex: 1, alignItems: 'center', gap: 6, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line }}>
              <ZIcon name={ic} size={20} color={c.brand} />
              <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{lb}</Text>
            </View>
          ))}
        </View>
      </View>
      <View style={{ paddingBottom: 24, paddingTop: 12 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>
    </View>
  );
};

export default Receipt;
