import type { HTMLAttributes } from "react";

import { cn, cva, type VariantProps } from "../../lib/cn";

const badgeVariants = cva(
    "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-[0.12em]",
    {
        variants: {
            variant: {
                default: "border-[var(--te-border)] bg-[var(--te-surface)] text-[var(--te-foreground)]",
                subtle: "border-[var(--te-border)] bg-transparent text-[var(--te-muted)]",
                accent: "border-[var(--te-accent)] bg-[var(--te-subtle-accent)] text-[var(--te-foreground)]",
                warning: "border-[var(--te-warning)] bg-transparent text-[var(--te-foreground)]",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    },
);

export interface BadgeProps
    extends HTMLAttributes<HTMLSpanElement>,
        VariantProps<typeof badgeVariants> {}

export function Badge(props: BadgeProps) {
    const { className, variant, ...rest } = props;
    return <span className={cn(badgeVariants({ variant }), className)} {...rest} />;
}
