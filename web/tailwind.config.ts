import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', '"Cascadia Code"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        'xs': ['12px', '16px'],
        'sm': ['13px', '20px'],
        'base': ['14px', '22px'],
      },
      colors: {
        surface: {
          0: '#111318',
          1: '#16181d',
          2: '#1c1e24',
          3: '#24262d',
        },
        border: {
          subtle: '#1e2028',
          DEFAULT: '#2a2d36',
        },
        'text-primary': '#e4e4e7',
        'text-secondary': '#a1a1aa',
        'text-tertiary': '#71717a',
        'text-muted': '#52525b',
        accent: {
          blue: '#3b82f6',
          'blue-soft': '#1e3a5f',
          green: '#22c55e',
          amber: '#f59e0b',
          red: '#ef4444',
          purple: '#a855f7',
        },
      },
      boxShadow: {
        'card': '0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2)',
        'elevated': '0 4px 12px rgba(0,0,0,0.4)',
        'focus': '0 0 0 2px rgba(59,130,246,0.3)',
        'modal': '0 8px 32px rgba(0,0,0,0.6)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
