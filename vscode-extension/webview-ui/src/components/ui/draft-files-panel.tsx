import { diffLines } from "diff";
import type { ReactNode } from "react";

import type { DraftFileViewModel } from "../../../src/types";
import { cn } from "../../lib/cn";
import { Badge } from "./badge";
import { Button } from "./button";
import { Card } from "./card";

export function DraftFilesPanel(props: {
    draftFiles: DraftFileViewModel[];
    onOpenFile?: (path: string) => void;
}) {
    if (props.draftFiles.length === 0) {
        return <p className="text-sm text-[var(--te-muted)]">No draft files yet.</p>;
    }

    return (
        <div className="space-y-3">
            {props.draftFiles.map((item) => {
                const hasBase = item.baseSource === "workspace_live" && item.baseContent !== null;
                return (
                    <Card key={item.filePath} className="overflow-hidden border-dashed">
                        <div className="flex items-center justify-between gap-3 border-b border-[var(--te-border)] px-4 py-3">
                            <div className="min-w-0">
                                <p className="truncate text-sm font-semibold">{item.filePath}</p>
                                <p className="text-xs text-[var(--te-muted)]">
                                    {hasBase ? "Live workspace diff" : "Draft text only"}
                                </p>
                            </div>
                            <div className="flex items-center gap-2">
                                <Badge variant={hasBase ? "accent" : "warning"}>
                                    {item.baseSource}
                                </Badge>
                                {props.onOpenFile ? (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => props.onOpenFile?.(item.filePath)}
                                    >
                                        Open
                                    </Button>
                                ) : null}
                            </div>
                        </div>
                        <div className="px-4 py-4">
                            {hasBase ? (
                                <pre className="overflow-x-auto rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                                    {renderDiff(item.baseContent ?? "", item.content)}
                                </pre>
                            ) : (
                                <pre className="overflow-x-auto rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                                    {item.content}
                                </pre>
                            )}
                        </div>
                    </Card>
                );
            })}
        </div>
    );
}

function renderDiff(baseContent: string, nextContent: string): ReactNode {
    const parts = diffLines(baseContent, nextContent);
    return parts.map((part, index) => {
        const prefix = part.added ? "+" : part.removed ? "-" : " ";
        const tone = part.added
            ? "text-[var(--vscode-gitDecoration-addedResourceForeground,#2ea043)]"
            : part.removed
                ? "text-[var(--vscode-gitDecoration-deletedResourceForeground,#d73a49)]"
                : "text-[var(--te-foreground)]";
        return (
            <span key={`${prefix}-${index}`} className={cn("block whitespace-pre-wrap", tone)}>
                {part.value
                    .split("\n")
                    .filter((line, lineIndex, lines) => {
                        return !(lineIndex === lines.length - 1 && line === "");
                    })
                    .map((line) => `${prefix} ${line}`)
                    .join("\n")}
                {"\n"}
            </span>
        );
    });
}
