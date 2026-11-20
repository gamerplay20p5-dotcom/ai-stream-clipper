const color = (token) => `rgb(var(--${token}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        suite: {
          DEFAULT: color("suite"), panel: color("panel"), raised: color("raised"),
          line: color("line"), outline: color("outline"), media: color("media"),
        },
        ink: { DEFAULT: color("ink"), muted: color("muted"), faint: color("faint") },
        tally: { DEFAULT: color("tally"), dim: color("tally-dim") },
        take: { DEFAULT: color("take"), dim: color("take-dim"), ink: color("take-ink") },
        action: { DEFAULT: color("action"), hover: color("action-hover"), ink: color("action-ink") },
        ok: color("ok"),
        auto: { DEFAULT: color("auto"), dim: color("auto-dim") },
        warning: { DEFAULT: color("warning"), dim: color("warning-dim") },
        processing: { DEFAULT: color("processing"), dim: color("processing-dim") },
      },
      borderRadius: { md: "var(--radius-control)", lg: "var(--radius-panel)" },
      fontFamily: {
        sans: ['"Archivo Variable"', "system-ui", "sans-serif"],
        mono: ['"Cascadia Code"', 'Consolas', "monospace"],
      },
      fontSize: {
        xs: ["0.75rem", "1.1rem"],
        sm: ["0.875rem", "1.3rem"],
        base: ["0.9375rem", "1.45rem"],
        lg: ["1.125rem", "1.55rem"],
        xl: ["1.375rem", "1.75rem"],
        "2xl": ["1.75rem", "2.05rem"],
        "3xl": ["2.375rem", "2.55rem"],
      },
      aspectRatio: { short: "9 / 16" },
    },
  },
  plugins: [],
};
