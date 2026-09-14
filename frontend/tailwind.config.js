/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ['class'],
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        // 保留旧 primary 避免破坏老代码（同步到新蓝色系）
        primary: { 50: '#eff8ff', 500: '#1f7fd6', 600: '#1668b3', 700: '#0e5fa8' },
        // 设计系统语义 token（浅色科技风）
        brand: {
          50: '#eff8ff', 100: '#e0f0fb', 200: '#bfddf5', 300: '#8ec8f0',
          400: '#4ba3e3', 500: '#1f7fd6', 600: '#1668b3', 700: '#0e5fa8', 900: '#16324a',
        },
        success: { 50: '#e9faf3', 300: '#a2e6cb', 500: '#2ebd85', 600: '#1fa06f' },
        warning: { 50: '#fdf6e9', 300: '#f3d9ae', 500: '#e9a23b', 600: '#b26a10', 700: '#8f5410' },
        danger: { 50: '#fdeeee', 300: '#f2b8b8', 400: '#ea8383', 500: '#e15a5a', 600: '#c74444' },
        // shadcn CSS 变量映射
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [require('@tailwindcss/forms'), require('tailwindcss-animate')],
}
