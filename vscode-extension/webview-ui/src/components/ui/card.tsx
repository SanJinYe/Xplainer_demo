import type { HTMLAttributes } from "react";

import { cn } from "../../lib/cn";

export function Card(props: HTMLAttributes<HTMLDivElement>) {
    const { className, ...rest } = props;
    return (
        <div
            className={cn(
                "rounded-[20px] border border-[var(--te-border)] bg-[var(--te-surface-muted)] shadow-[0_12px_32px_rgba(0,0,0,0.08)]",
                className,
            )}
            {...rest}
        />
    );
}
