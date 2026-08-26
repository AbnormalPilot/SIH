/** @type {import('tailwindcss').Config} */
//
// Colours resolve through CSS custom properties holding space-separated RGB
// channels, so a single `data-theme` swap on <html> retheres the whole page and
// Tailwind's opacity modifiers (bg-accent/10, border-hair/60) still work.
const c = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Geist Variable"', "system-ui", "sans-serif"],
        mono: ['"Geist Mono Variable"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        plane: c("plane"),
        surface: c("surface"),
        raised: c("raised"),
        hair: c("hair"),
        ink: c("ink"),
        ink2: c("ink2"),
        muted: c("muted"),
        accent: c("accent"),
        accentSoft: c("accent-soft"),
        // Status colours are fixed and never themed.
        good: c("good"),
        warn: c("warn"),
        bad: c("bad"),
      },
      maxWidth: { shell: "1240px" },
      letterSpacing: { tightest: "-0.035em" },
    },
  },
  plugins: [],
};
