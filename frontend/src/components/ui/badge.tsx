import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-brand-600 text-white hover:bg-brand-700',
        secondary: 'border-transparent bg-slate-100 text-slate-900 hover:bg-slate-200',
        destructive: 'border-transparent bg-danger-500 text-white hover:bg-danger-500/80',
        success: 'border-transparent bg-success-50 text-success-500',
        warning: 'border-transparent bg-warning-50 text-warning-500',
        outline: 'text-slate-900 border-slate-200',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
