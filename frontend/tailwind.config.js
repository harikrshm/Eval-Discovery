/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  future: {
    hoverOnlyWhenSupported: true,
  },
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        muted: {
          DEFAULT: "var(--muted)",
          foreground: "var(--muted-foreground)",
        },
        border: "var(--border)",
        input: "var(--border-input)",
        card: "var(--card)",
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          foreground: "var(--accent-foreground)",
        },
      },
      ringColor: {
        DEFAULT: "var(--ring)",
      },
    },
  },
  plugins: [],
}
