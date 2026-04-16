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
        'xs': ['13px', '18px'],
        'sm': ['14px', '22px'],
        'base': ['15px', '24px'],
      },
      colors: {
        surface: {
          0: '#13151a',
          1: '#1a1c23',
          2: '#22242b',
          3: '#2c2e36',
        },
        border: {
          subtle: '#2a2d36',
          DEFAULT: '#383b45',
        },
        'text-primary': '#e4e4e7',
        'text-secondary': '#a1a1aa',
        'text-tertiary': '#8a8a96',
        'text-muted': '#6b6b78',
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
        'card': '0 2px 8px rgba(0,0,0,0.4), 0 1px 3px rgba(0,0,0,0.3)',
        'elevated': '0 8px 24px rgba(0,0,0,0.5), 0 2px 8px rgba(0,0,0,0.3)',
        'focus': '0 0 0 2px rgba(99,102,241,0.4)',
        'modal': '0 16px 48px rgba(0,0,0,0.7), 0 4px 16px rgba(0,0,0,0.4)',
        'glow-blue': '0 0 12px rgba(59,130,246,0.15)',
        'glow-purple': '0 0 12px rgba(168,85,247,0.15)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
