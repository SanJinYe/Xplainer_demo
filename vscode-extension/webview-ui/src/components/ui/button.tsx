import type { ButtonHTMLAttributes } from "react";

import { cn, cva, type VariantProps } from "../../lib/cn";

const buttonVariants = cva(
    "inline-flex items-center justify-center rounded-full border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    {
        variants: {
            variant: {
                primary: "border-[var(--te-accent)] bg-[var(--te-accent)] text-[var(--te-accent-foreground)]",
                ghost: "border-[var(--te-border)] bg-transparent text-[var(--te-foreground)] hover:border-[var(--te-border-strong)]",
                subtle: "border-[var(--te-border)] bg-[var(--te-surface)] text-[var(--te-foreground)] hover:border-[var(--te-border-strong)]",
                danger: "border-[var(--te-danger)] bg-transparent text-[var(--te-danger)]",
            },
            size: {
                sm: "px-2.5 py-1 text-xs",
                md: "px-3 py-1.5 text-sm",
            },
        },
        defaultVariants: {
            variant: "subtle",
            size: "md",
        },
    },
);

export interface ButtonProps
    extends ButtonHTMLAttributes<HTMLButtonElement>,
        VariantProps<typeof buttonVariants> {}

export function Button(props: ButtonProps) {
    const { className, variant, size, type, ...rest } = props;
    return (
        <button
            type={type ?? "button"}
            className={cn(buttonVariants({ variant, size }), className)}
            {...rest}
        />
    );
}
