import type { HTMLAttributes } from "react";

import { cn } from "../../lib/cn";

export function Chip(props: HTMLAttributes<HTMLDivElement>) {
    const { className, ...rest } = props;
    return (
        <div
            className={cn(
                "inline-flex items-center gap-2 rounded-full border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-1.5 text-xs text-[var(--te-foreground)]",
                className,
            )}
            {...rest}
        />
    );
}
