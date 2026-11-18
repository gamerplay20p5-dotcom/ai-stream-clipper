/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        suite: { DEFAULT: "#232428", panel: "#2C2D32", raised: "#34353B", line: "#3E3F46" },
        ink: { DEFAULT: "#ECEAE4", muted: "#9B9AA1", faint: "#6E6E76" },
        tally: { DEFAULT: "#E5484D", dim: "#3A2326" },
        take: { DEFAULT: "#F2B33D", dim: "#3B3220", ink: "#1E1A12" },
        ok: "#6FBF8E",
      },
      fontFamily: {
        sans: ['"Archivo Variable"', "system-ui", "sans-serif"],
      },
      fontSize: {
        xs: ["0.75rem", "1.1rem"],
        sm: ["0.8125rem", "1.25rem"],
        base: ["0.9375rem", "1.45rem"],
        lg: ["1.125rem", "1.6rem"],
        xl: ["1.5rem", "1.9rem"],
        "2xl": ["2rem", "2.3rem"],
        "3xl": ["2.75rem", "3rem"],
      },
      aspectRatio: { short: "9 / 16" },
    },
  },
  plugins: [],
};
