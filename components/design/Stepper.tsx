import React from 'react';
import { View, Text } from 'react-native';
import { useTheme, font } from '@/lib/theme';

/**
 * A slim onboarding progress indicator: a labelled row ("Step 2 of 4") over a
 * segmented bar that fills as the customer advances. Purely presentational and
 * theme-aware — pass the current step (1-based) and the total.
 *
 * Used across the account-creation flow (register → verify → password → PIN) so
 * a new customer always knows where they are and how much is left, which lifts
 * completion on the multi-screen sign-up.
 */
export const Stepper = ({
  step,
  total,
  label,
}: {
  step: number;
  total: number;
  label?: string;
}) => {
  const { c } = useTheme();
  const done = Math.max(0, Math.min(step, total));
  return (
    <View
      style={{ marginBottom: 22 }}
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={label || `Step ${done} of ${total}`}
      accessibilityValue={{ min: 0, max: total, now: done }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 9 }}>
        <Text style={{ fontSize: 12.5, fontFamily: font.semibold, color: c.ink3 }}>
          {label || `Step ${done} of ${total}`}
        </Text>
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.brand }}>
          {Math.round((done / total) * 100)}%
        </Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 6 }}>
        {Array.from({ length: total }).map((_, i) => (
          <View
            key={i}
            style={{
              flex: 1,
              height: 6,
              borderRadius: 999,
              backgroundColor: i < done ? c.brand : c.surface3,
            }}
          />
        ))}
      </View>
    </View>
  );
};

export default Stepper;
