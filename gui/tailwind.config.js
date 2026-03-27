/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts}'
  ],
  theme: {
    extend: {
      colors: {
        // Custom surface palette for dark dashboard
        surface: {
          950: '#090b11',
          900: '#0f1117',
          800: '#161b27',
          700: '#1e2435',
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
