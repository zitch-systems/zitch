/** @type {import('tailwindcss').Config} */
// Zitch brand color tokens below are sourced from the design handoff:
// docs/design_handoff_zitch_revamp/assets/tokens.css
module.exports = {
  content: ["./app/**/*.{js,jsx,ts,tsx}", "./components/**/*.{js,jsx,ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        primary: "#161622",
        secondary: {
          DEFAULT: "#FF9C01",
          100: "#FF9001",
          200: "#FF8E01",
        },
        black: {
          DEFAULT: "#000",
          100: "#1E1E2D",
          200: "#232533",
        },
        gray: {
          100: "#CDCDE0",
        },
        // ---- Zitch brand tokens (design revamp) ----
        teal: {
          50: "#E6F7F4",
          100: "#C2EDE7",
          200: "#8FDDD4",
          300: "#54C9BD",
          400: "#23B1A8",
          500: "#0FA295", // primary brand
          600: "#00847B", // deep brand
          700: "#066E66",
          800: "#0C5249",
          900: "#073A34",
          950: "#04221F",
        },
        zcyan: "#5CF5EB",
        zink: "#04201C",
        znavy: "#02344A",
        success: "#00B51D",
        warning: "#F5A623",
        danger: "#FF3B3B",
      },
      fontFamily: {
        pthin: ["Poppins-Thin", "sans-serif"],
        pextralight: ["Poppins-ExtraLight", "sans-serif"],
        plight: ["Poppins-Light", "sans-serif"],
        pregular: ["Poppins-Regular", "sans-serif"],
        pmedium: ["Poppins-Medium", "sans-serif"],
        psemibold: ["Poppins-SemiBold", "sans-serif"],
        pbold: ["Poppins-Bold", "sans-serif"],
        pextrabold: ["Poppins-ExtraBold", "sans-serif"],
        pblack: ["Poppins-Black", "sans-serif"],
      },
    },
  },
  plugins: [],
};
