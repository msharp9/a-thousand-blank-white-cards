import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border-2 border-transparent bg-clip-padding text-sm font-extrabold whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-x-px active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "border-ink bg-primary text-primary-foreground sticker-shadow hover:bg-primary active:not-aria-[haspopup]:shadow-[2px_2px_0_#1a1a1a]",
        outline:
          "border-ink bg-white text-foreground sticker-shadow hover:bg-muted aria-expanded:bg-muted active:not-aria-[haspopup]:shadow-[2px_2px_0_#1a1a1a]",
        secondary:
          "border-ink bg-white text-foreground sticker-shadow hover:bg-muted aria-expanded:bg-muted active:not-aria-[haspopup]:shadow-[2px_2px_0_#1a1a1a]",
        accent:
          "border-ink bg-accent text-accent-foreground sticker-shadow hover:bg-accent/90 active:not-aria-[haspopup]:shadow-[2px_2px_0_#1a1a1a]",
        ghost:
          "border-transparent hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground active:not-aria-[haspopup]:translate-x-0 active:not-aria-[haspopup]:translate-y-0",
        destructive:
          "border-ink bg-destructive text-white sticker-shadow hover:bg-destructive/90 active:not-aria-[haspopup]:shadow-[2px_2px_0_#1a1a1a]",
        link: "border-transparent text-primary underline-offset-4 hover:underline active:not-aria-[haspopup]:translate-x-0 active:not-aria-[haspopup]:translate-y-0",
      },
      size: {
        default:
          "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-6 gap-1 rounded-[min(var(--radius-md),10px)] px-2 text-xs in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 rounded-[min(var(--radius-md),12px)] px-2.5 text-[0.8rem] in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        icon: "size-8",
        "icon-xs":
          "size-6 rounded-[min(var(--radius-md),10px)] in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7 rounded-[min(var(--radius-md),12px)] in-data-[slot=button-group]:rounded-lg",
        "icon-lg": "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
