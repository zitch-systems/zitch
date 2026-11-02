import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { Field } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

/** A text field with a matching list to pick from; anything can still be typed. */
export function ComboField({ label, value, options, onChange, editable = true }: {
  label: string; value: string; options: string[]; onChange: (value: string) => void; editable?: boolean;
}) {
  const { c } = useTheme();
  const [open, setOpen] = useState(false);
  const query = value.trim().toLowerCase();
  const matches = options.filter((o) => o.toLowerCase().includes(query) && o.toLowerCase() !== query).slice(0, 6);
  return <View>
    <Field label={label} value={value} editable={editable} autoCorrect={false}
      onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} onChangeText={(text) => { onChange(text); setOpen(true); }} />
    {open && editable && matches.length > 0 && <View accessibilityRole="list" style={{ marginTop: 6, borderRadius: 14, borderWidth: 1, borderColor: c.line, backgroundColor: c.surface, overflow: 'hidden' }}>
      {matches.map((option, i) => <Pressable key={option} accessibilityRole="button" accessibilityLabel={option}
        onPress={() => { onChange(option); setOpen(false); }}
        style={{ paddingVertical: 12, paddingHorizontal: 14, borderTopWidth: i ? 1 : 0, borderTopColor: c.line }}>
        <Text style={{ color: c.ink1, fontFamily: font.regular, fontSize: 15 }}>{option}</Text>
      </Pressable>)}
    </View>}
  </View>;
}
