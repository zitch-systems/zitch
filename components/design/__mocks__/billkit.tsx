// Test double for the bill-screen pieces: plain, labelled controls so screen
// tests can type into fields and pick networks, discos and plans by name.
import React from 'react';
import { View, Text, TextInput, Pressable } from 'react-native';

const NAMES: Record<string, string> = { '1': 'MTN', '2': 'GLO', '3': 'Airtel', '4': '9mobile' };
const pick = (items: { id: string; name: string }[], onPick: (id: string) => void) =>
  React.createElement(View, null, ...items.map((it) => React.createElement(
    Pressable, { key: it.id, accessibilityLabel: it.name, onPress: () => onPick(it.id) }, React.createElement(Text, null, it.name))));

export const NETWORK_LOGOS: Record<string, any> = {};
export const NETWORK_LABELS: Record<string, string> = { '1': 'MTN', '2': 'Glo', '3': 'Airtel', '4': '9mobile' };
export const useFieldTint = () => 'transparent';
export const SectionTitle = () => null;
export const Tick = () => null;
export const NetDot = () => null;
export const AmountGrid = () => null;
export const PillTabs = () => null;
export const NetworkCircles = ({ onPick }: { onPick: (id: string) => void }) =>
  pick(Object.keys(NAMES).map((id) => ({ id, name: NAMES[id] })), onPick);
export const DiscoRow = ({ items, onPick }: { items: { id: string; name: string }[]; onPick: (id: string) => void }) => pick(items, onPick);
export const PlanGrid = ({ plans, onPick }: { plans: { id: string; label: string }[]; onPick: (id: string) => void }) =>
  pick(plans.map((p) => ({ id: p.id, name: p.label })), onPick);
export const ResolvedAccount = ({ name }: { name: string }) => React.createElement(Text, null, `✓ ${name}`);
export const RecentField = ({ label, value, onChangeText, below }: { label: string; value: string; onChangeText: (v: string) => void; below?: React.ReactNode }) =>
  React.createElement(View, null, React.createElement(TextInput, { accessibilityLabel: label, value, onChangeText }), below || null);
