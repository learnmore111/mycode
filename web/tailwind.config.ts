import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        'xxs': ['10.5px', '15px'],
        'xs': ['12px', '17px'],
        'sm': ['13px', '20px'],
        'base': ['14px', '22px'],
        'md': ['15px', '24px'],
        'lg': ['17px', '26px'],
      },
      colors: {
        surface: {
          0: '#FFFFFF',
          1: '#FAFAF8',
          2: '#F4F3F0',
          3: '#EEEDEA',
          4: '#E5E4E0',
          hover: '#F0EFEC',
          active: '#E8E7E3',
        },
        ink: {
          DEFAULT: '#1A1A1A',
          strong: '#0F0F0F',
          secondary: '#5C5C5C',
          tertiary: '#8C8C8C',
          muted: '#ABABAB',
          faint: '#CDCDCD',
          ghost: '#E0E0E0',
        },
        line: {
          DEFAULT: '#E5E4E0',
          subtle: '#EEEDEA',
          strong: '#D4D3CF',
        },
        accent: {
          DEFAULT: '#3D3BF3',
          hover: '#3331D4',
          light: '#EDEDFE',
          muted: '#8887F7',
        },
        status: {
          success: '#16A34A',
          'success-light': '#F0FDF4',
          warning: '#CA8A04',
          'warning-light': '#FEFCE8',
          error: '#DC2626',
          'error-light': '#FEF2F2',
          info: '#2563EB',
          'info-light': '#EFF6FF',
        },
      },
      borderRadius: {
        'sm': '4px',
        'md': '6px',
        'lg': '8px',
        'xl': '12px',
      },
      boxShadow: {
        'xs': '0 1px 2px rgba(0,0,0,0.04)',
        'sm': '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
        'md': '0 4px 12px rgba(0,0,0,0.06)',
        'lg': '0 8px 24px rgba(0,0,0,0.08)',
        'overlay': '0 16px 40px rgba(0,0,0,0.12)',
      },
      animation: {
        'blink': 'blink 1s step-end infinite',
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-up': 'slideUp 0.25s cubic-bezier(0.16,1,0.3,1)',
        'slide-in-right': 'slideInRight 0.25s cubic-bezier(0.16,1,0.3,1)',
        'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
      },
      keyframes: {
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(12px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '0.4' },
          '50%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
} satisfies Config
