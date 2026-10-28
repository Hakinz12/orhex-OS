import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        surface: {
          DEFAULT: "var(--surface)",
          raised: "var(--surface-raised)",
          inset: "var(--background-raised)",
        },
        brand: {
          DEFAULT: "var(--accent)",
          strong: "var(--accent-strong)",
          ink: "var(--accent-ink)",
        }
      },
      fontFamily: {
        sans: ["Aptos", "Segoe UI", "sans-serif"],
        display: ["Aptos Display", "Segoe UI", "sans-serif"],
      },
      borderRadius: {
        card: "var(--radius-card)",
      },
      boxShadow: {
        panel: "var(--shadow-panel)",
      },
    },
  },
  plugins: [],
};
export default config;
