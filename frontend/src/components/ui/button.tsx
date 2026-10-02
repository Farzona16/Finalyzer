import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'interactive inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium ' +
    'transition-all duration-200 ease-in-out disabled:pointer-events-none disabled:opacity-50 ' +
    'disabled:hover:scale-100',
  {
    variants: {
      variant: {
        primary:
          'bg-blue text-white shadow-lg shadow-blue/20 hover:brightness-110 hover:shadow-blue/30',
        secondary: 'bg-surface-2 text-text hover:bg-border',
        ghost: 'text-muted hover:bg-surface-2 hover:text-text',
        outline: 'border border-border/40 text-text hover:border-blue/40 hover:bg-surface-2',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
