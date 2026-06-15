import React, { createContext, useContext, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  Inter_800ExtraBold,
} from '@expo-google-fonts/inter';

// ---- App font map (Inter; loaded in app/_layout.tsx) ----
export const appFonts = {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  Inter_800ExtraBold,
};

// font-family by weight — RN needs the exact variant, not a numeric weight
export const font = {
  regular: 'Inter_400Regular',
  medium: 'Inter_500Medium',
  semibold: 'Inter_600SemiBold',
  bold: 'Inter_700Bold',
  extrabold: 'Inter_800ExtraBold',
};

// ---- Brand ramp (from docs/design_handoff_zitch_revamp/assets/tokens.css) ----
export const palette = {
  teal50: '#E6F7F4',
  teal100: '#C2EDE7',
  teal200: '#8FDDD4',
  teal300: '#54C9BD',
  teal400: '#23B1A8',
  teal500: '#0FA295',
  teal600: '#00847B',
  teal700: '#066E66',
  teal800: '#0C5249',
  teal900: '#073A34',
  teal950: '#04221F',
  cyan: '#5CF5EB',
  cyanSoft: '#A9FBF4',
  ink: '#04201C',
  navy: '#02344A',
  lime: '#00B51D',
  amber: '#F5A623',
  red: '#FF3B3B',
  violet: '#7A5CFF',
  netMtn: '#FFCC00',
  netAirtel: '#E40000',
  netGlo: '#2BB24C',
  net9mobile: '#0A8A3D',
};

// ---- Semantic tokens per theme (the .z-light / .z-dark blocks) ----
export type ThemeTokens = {
  bg: string;
  bgGradient: [string, string, string];
  surface: string;
  surface2: string;
  surface3: string;
  line: string;
  ink1: string;
  ink2: string;
  ink3: string;
  inkOnBrand: string;
  brand: string;
  brandDeep: string;
  heroGradient: [string, string, string];
  // shared palette passthrough
  cyan: string;
  lime: string;
  amber: string;
  red: string;
  violet: string;
};

export const light: ThemeTokens = {
  bg: '#EFF7F5',
  bgGradient: ['#DDF3EF', '#EFF7F5', '#F5FAF9'],
  surface: '#FFFFFF',
  surface2: '#F4F9F8',
  surface3: '#EAF3F1',
  line: '#E2EEEB',
  ink1: '#0A0A0B',
  ink2: '#3A434A',
  ink3: '#737B83',
  inkOnBrand: '#FFFFFF',
  brand: palette.teal500,
  brandDeep: palette.teal600,
  heroGradient: ['#0C5249', '#00847B', '#0FA295'],
  cyan: palette.cyan,
  lime: palette.lime,
  amber: palette.amber,
  red: palette.red,
  violet: palette.violet,
};

export const dark: ThemeTokens = {
  bg: '#05201C',
  bgGradient: ['#0A3A33', '#06251F', '#041714'],
  surface: '#0B2A24',
  surface2: '#0F332C',
  surface3: '#143C34',
  line: '#1B463C',
  ink1: '#EAFBF7',
  ink2: '#A6C9C1',
  ink3: '#6F9189',
  inkOnBrand: '#04221F',
  brand: palette.teal400,
  brandDeep: palette.teal500,
  heroGradient: ['#073A34', '#00847B', '#12B7AA'],
  cyan: palette.cyan,
  lime: palette.lime,
  amber: palette.amber,
  red: palette.red,
  violet: palette.violet,
};

// ---- Radii ----
export const radius = { sm: 12, md: 18, lg: 24, xl: 30, pill: 999 };

type ThemeName = 'light' | 'dark';
type ThemeContextValue = {
  theme: ThemeName;
  c: ThemeTokens;
  setTheme: (t: ThemeName) => void;
  toggle: () => void;
};

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'light',
  c: light,
  setTheme: () => {},
  toggle: () => {},
});

const STORAGE_KEY = 'z-theme';

export const ThemeProvider = ({ children }: { children: React.ReactNode }) => {
  const [theme, setThemeState] = useState<ThemeName>('light');

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY).then((v) => {
      if (v === 'light' || v === 'dark') setThemeState(v);
    });
  }, []);

  const setTheme = (t: ThemeName) => {
    setThemeState(t);
    AsyncStorage.setItem(STORAGE_KEY, t);
  };
  const toggle = () => setTheme(theme === 'dark' ? 'light' : 'dark');

  const c = theme === 'dark' ? dark : light;

  return (
    <ThemeContext.Provider value={{ theme, c, setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
};

export const useTheme = () => useContext(ThemeContext);
