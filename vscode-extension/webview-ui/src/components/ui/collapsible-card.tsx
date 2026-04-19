import * as Collapsible from "@radix-ui/react-collapsible";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { Badge } from "./badge";
import { Button } from "./button";
import { Card } from "./card";

export function CollapsibleCard(props: {
    id: string;
    title: string;
    description?: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    children: ReactNode;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <Collapsible.Root open={props.open} onOpenChange={props.onOpenChange} asChild>
            <Card className={cn("overflow-hidden", props.className)}>
                <div>
                    <div className="flex items-start justify-between gap-3 border-b border-[var(--te-border)] px-4 py-3">
                        <div className="space-y-1">
                            <p className="text-sm font-semibold">{props.title}</p>
                            {props.description ? (
                                <p className="text-xs text-[var(--te-muted)]">{props.description}</p>
                            ) : null}
                        </div>
                        <div className="flex items-center gap-2">
                            {props.action}
                            <Collapsible.Trigger asChild>
                                <Button variant="ghost" size="sm">
                                    <Badge variant="subtle">{props.open ? "open" : "closed"}</Badge>
                                </Button>
                            </Collapsible.Trigger>
                        </div>
                    </div>
                    <Collapsible.Content className="px-4 py-4">
                        {props.children}
                    </Collapsible.Content>
                </div>
            </Card>
        </Collapsible.Root>
    );
}
