import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default:
          'bg-gradient-to-br from-[#1f7fd6] to-[#35b9e9] text-white shadow-[0_4px_14px_rgba(31,127,214,0.35),inset_0_1px_0_rgba(255,255,255,0.35)] hover:brightness-105',
        destructive: 'bg-danger-500 text-white hover:bg-danger-500/90',
        outline: 'border border-[#bfdcf3] bg-white text-[#1f7fd6] hover:bg-[#f2f9ff]',
        secondary: 'bg-[#e3f1fb] text-[#0e5fa8] hover:bg-[#d3e9fa]',
        ghost: 'hover:bg-[#eaf5fd] hover:text-[#0e5fa8]',
        link: 'text-[#1f7fd6] underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-md px-3',
        lg: 'h-11 rounded-md px-8',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
