import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { Field } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

/** A text field with a scrollable list to pick from; anything can still be typed. */
export function ComboField({ label, value, options, onChange, editable = true, error }: {
  label: string; value: string; options: string[]; onChange: (value: string) => void; editable?: boolean; error?: string;
}) {
  const { c } = useTheme();
  const [open, setOpen] = useState(false);
  const query = value.trim().toLowerCase();
  const exact = options.some((o) => o.toLowerCase() === query);
  // An exact pick shows the full list again on the next tap, so it can be changed.
  const matches = exact ? options : options.filter((o) => o.toLowerCase().includes(query));
  const pick = (option: string) => { onChange(option); setOpen(false); };
  return <View>
    <Field label={label} value={value} editable={editable} autoCorrect={false} error={error}
      onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 200)}
      onChangeText={(text) => { onChange(text); setOpen(true); }} />
    {open && editable && matches.length > 0 && <View style={{ marginTop: 6, borderRadius: 14, borderWidth: 1, borderColor: c.line,
      backgroundColor: c.surface, overflow: 'hidden', maxHeight: 264 }}>
      <ScrollView nestedScrollEnabled keyboardShouldPersistTaps="handled" accessibilityRole="list">
        {matches.map((option, i) => <Pressable key={option} accessibilityRole="button" accessibilityLabel={option}
          onPress={() => pick(option)}
          style={{ paddingVertical: 12, paddingHorizontal: 14, borderTopWidth: i ? 1 : 0, borderTopColor: c.line,
            backgroundColor: option.toLowerCase() === query ? c.surface2 : undefined }}>
          <Text style={{ color: c.ink1, fontFamily: font.regular, fontSize: 15 }}>{option}</Text>
        </Pressable>)}
      </ScrollView>
    </View>}
  </View>;
}
