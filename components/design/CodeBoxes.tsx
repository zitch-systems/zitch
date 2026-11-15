import React, { forwardRef, useRef, useImperativeHandle } from 'react';
import { View, Text, TextInput, Pressable } from 'react-native';
import { useTheme, font } from '@/lib/theme';

/**
 * A one-time code shown as boxes, one digit each.
 *
 * The boxes are display-only; a hidden numeric TextInput captures input from the
 * device keypad (and OS SMS autofill). Tapping the boxes focuses it. Used by the
 * phone verification page and sign-up's BVN code.
 */
export type CodeBoxesHandle = { focus: () => void };

type Props = {
  value: string;
  onChange: (code: string) => void;
  length?: number;
  editable?: boolean;
  autoFocus?: boolean;
  accessibilityLabel: string;
  accessibilityHint?: string;
  marginTop?: number;
  marginBottom?: number;
};

export const CodeBoxes = forwardRef<CodeBoxesHandle, Props>(({
  value, onChange, length = 6, editable = true, autoFocus = false,
  accessibilityLabel, accessibilityHint, marginTop = 22, marginBottom = 18,
}, ref) => {
  const { c } = useTheme();
  const inputRef = useRef<TextInput>(null);
  useImperativeHandle(ref, () => ({ focus: () => inputRef.current?.focus() }), []);

  return (
    <Pressable onPress={() => inputRef.current?.focus()}>
      <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ flexDirection: 'row', gap: 10, marginTop, marginBottom }}>
        {Array.from({ length }).map((_, k) => (
          <View
            key={k}
            style={{
              flex: 1,
              height: 58,
              borderRadius: 14,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: c.surface,
              borderWidth: 2,
              borderColor: value.length === k ? c.brand : c.line,
            }}
          >
            <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>{value[k] || ''}</Text>
          </View>
        ))}
      </View>

      <TextInput
        ref={inputRef}
        accessibilityLabel={accessibilityLabel}
        accessibilityHint={accessibilityHint}
        value={value}
        onChangeText={(t) => onChange(t.replace(/\D/g, '').slice(0, length))}
        keyboardType="number-pad"
        inputMode="numeric"
        maxLength={length}
        autoFocus={autoFocus}
        caretHidden
        editable={editable}
        textContentType="oneTimeCode"
        autoComplete="sms-otp"
        importantForAutofill="yes"
        // Cover the boxes so taps focus it; invisible so only the boxes show.
        // Keep the native field visually hidden without opacity:0. Some Android
        // autofill services ignore a fully transparent OTP target after resend.
        style={{ position: 'absolute', top: marginTop, left: 0, right: 0, height: 58, color: 'transparent', backgroundColor: 'transparent' }}
      />
    </Pressable>
  );
});
CodeBoxes.displayName = 'CodeBoxes';

/** "Resend in 0:24" style label for a countdown in seconds. */
export const resendLabel = (seconds: number) =>
  `Resend in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
