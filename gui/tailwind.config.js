/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts}'
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Surface palette driven by CSS variables (adapts to light/dark theme)
        surface: {
          950: 'var(--surface-950)',
          900: 'var(--surface-900)',
          800: 'var(--surface-800)',
          700: 'var(--surface-700)',
        }
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      }
    }
  },
  plugins: []
}
