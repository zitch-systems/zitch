import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { BANK_WHATSAPP_DISPLAY } from '@/components/configFiles/links';
import { useTheme, font } from '@/lib/theme';

const WA_GREEN = '#25D366';


const WhatsAppBankingPromo = ({
  onPress,
  receipt = false,
}: {
  onPress?: () => void;
  receipt?: boolean;
}) => {
  const { c } = useTheme();

  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      accessibilityLabel={onPress ? 'Set up Zitch banking on WhatsApp' : undefined}
      style={({ pressed }) => ({
        borderRadius: 20,
        borderCurve: 'continuous',
        borderWidth: 1,
        borderColor: 'rgba(37,211,102,.32)',
        backgroundColor: c.surface2,
        padding: receipt ? 14 : 16,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 13,
        opacity: pressed ? 0.88 : 1,
      })}
    >
      <View
        style={{
          width: receipt ? 42 : 48,
          height: receipt ? 42 : 48,
          borderRadius: receipt ? 13 : 15,
          backgroundColor: WA_GREEN,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <WhatsAppGlyph size={receipt ? 23 : 27} color="#fff" />
      </View>

      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ color: WA_GREEN, fontFamily: font.bold, fontSize: 10.5, letterSpacing: 0.7 }}>
          ZITCH WHATSAPP BANKING
        </Text>
        <Text style={{ color: c.ink1, fontFamily: font.bold, fontSize: receipt ? 13.5 : 15, marginTop: 2 }}>
          Bank wherever you chat
        </Text>
        <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: receipt ? 11 : 12, lineHeight: receipt ? 16 : 17, marginTop: 2 }}>
          Send money, buy airtime and check your balance on WhatsApp.
        </Text>
        {receipt ? (
          <Text selectable style={{ color: c.ink2, fontFamily: font.semibold, fontSize: 11, marginTop: 4 }}>
            {BANK_WHATSAPP_DISPLAY}
          </Text>
        ) : null}
      </View>

      {onPress ? (
        <View style={{ borderRadius: 999, backgroundColor: WA_GREEN, paddingHorizontal: receipt ? 11 : 14, paddingVertical: 8 }}>
          <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 11.5 }}>
            {receipt ? 'Try it' : 'Set up'}
          </Text>
        </View>
      ) : null}
    </Pressable>
  );
};

export default WhatsAppBankingPromo;
