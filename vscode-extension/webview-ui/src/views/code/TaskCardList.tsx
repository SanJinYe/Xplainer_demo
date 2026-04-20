import { diffLines } from "diff";
import { useMemo, useState } from "react";

import type {
    AssistantFileChangeViewModel,
    CodeConversationMessageViewModel,
    CodeConversationRunViewModel,
} from "../../../../src/types";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/cn";

export function TaskCardList(props: {
    runs: CodeConversationRunViewModel[];
    onOpenDiff: (path: string) => void;
    onOpenFile: (path: string) => void;
}) {
    if (props.runs.length === 0) {
        return null;
    }

    return (
        <div className="space-y-5">
            {props.runs.map((run) => {
                return (
                    <section key={run.runId} className="space-y-3">
                        <RunDivider run={run} />
                        {run.messages.map((message) => {
                            return (
                                <ConversationMessage
                                    key={message.id}
                                    run={run}
                                    message={message}
                                    onOpenDiff={props.onOpenDiff}
                                    onOpenFile={props.onOpenFile}
                                />
                            );
                        })}
                    </section>
                );
            })}
        </div>
    );
}

function RunDivider(props: { run: CodeConversationRunViewModel }) {
    const metadata = [
        props.run.targetFilePath,
        props.run.launchMode,
        props.run.sourceTaskId ? `source ${props.run.sourceTaskId}` : null,
    ].filter(Boolean);

    return (
        <div className="flex items-center gap-3 py-1">
            <div className="h-px flex-1 bg-[var(--te-border)]" />
            <span className="shrink-0 text-[11px] uppercase tracking-[0.14em] text-[var(--te-muted)]">
                {metadata.length > 0 ? metadata.join(" / ") : "new run"}
            </span>
            <div className="h-px flex-1 bg-[var(--te-border)]" />
        </div>
    );
}

function ConversationMessage(props: {
    run: CodeConversationRunViewModel;
    message: CodeConversationMessageViewModel;
    onOpenDiff: (path: string) => void;
    onOpenFile: (path: string) => void;
}) {
    const [expanded, setExpanded] = useState<boolean>(
        props.message.kind === "assistant_working" && props.run.status === "running",
    );
    const hasDetails = useMemo(() => hasTurnDetails(props.message), [props.message]);

    const alignmentClassName =
        props.message.kind === "user_turn" ? "justify-end" : "justify-start";
    const bubbleClassName = cn(
        "max-w-[94%] rounded-[18px] border px-4 py-3 shadow-none",
        props.message.kind === "user_turn"
            ? "border-[var(--te-border-strong)] bg-[var(--te-subtle-accent)]"
            : props.message.kind === "assistant_error"
                ? "border-[var(--te-danger)] bg-[var(--te-surface)]"
                : "border-[var(--te-border)] bg-[var(--te-surface)]",
    );

    return (
        <div className={cn("flex", alignmentClassName)}>
            <article className={bubbleClassName}>
                <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--te-muted)]">
                            {props.message.kind === "user_turn" ? "You" : "Assistant"}
                        </p>
                        <Badge variant={resolveMessageBadgeVariant(props.message.kind, props.run.status)}>
                            {resolveMessageBadgeLabel(props.message.kind, props.run.status)}
                        </Badge>
                    </div>
                    {hasDetails ? (
                        <Button
                            variant="ghost"
                            size="sm"
                            className="border-transparent px-2"
                            onClick={() => setExpanded((current) => !current)}
                        >
                            {expanded ? "Hide details" : "View details"}
                        </Button>
                    ) : null}
                </div>
                <p
                    className={cn(
                        "mt-2 whitespace-pre-wrap text-sm leading-6",
                        props.message.kind === "assistant_error" ? "text-[var(--te-danger)]" : undefined,
                    )}
                >
                    {props.message.text}
                </p>
                {expanded && props.message.details ? (
                    <div className="mt-3 space-y-3 border-t border-[var(--te-border)] pt-3">
                        {props.message.details.reasoningSummary ? (
                            <DetailsSection title="Reasoning summary">
                                <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-[var(--te-muted)]">
                                    {props.message.details.reasoningSummary}
                                </pre>
                            </DetailsSection>
                        ) : null}
                        {props.message.details.toolTrace.length > 0 ? (
                            <DetailsSection title="Tool trace">
                                <div className="space-y-2">
                                    {props.message.details.toolTrace.map((item) => {
                                        return (
                                            <div
                                                key={item.stepId}
                                                className="rounded-[12px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2"
                                            >
                                                <div className="flex items-center justify-between gap-3">
                                                    <div className="flex items-center gap-2">
                                                        <Badge variant="subtle">{item.stepKind}</Badge>
                                                        <Badge variant="subtle">{item.status}</Badge>
                                                    </div>
                                                    <span className="truncate text-[11px] text-[var(--te-muted)]">
                                                        {item.filePath}
                                                    </span>
                                                </div>
                                                <p className="mt-2 text-xs leading-6 text-[var(--te-muted)]">
                                                    {item.summary}
                                                </p>
                                            </div>
                                        );
                                    })}
                                </div>
                            </DetailsSection>
                        ) : null}
                        {props.message.details.fileChanges.length > 0 ? (
                            <DetailsSection title="File changes">
                                <div className="space-y-3">
                                    {props.message.details.fileChanges.map((item) => {
                                        const preview = buildDiffPreview(item);
                                        return (
                                            <div
                                                key={item.filePath}
                                                className="rounded-[12px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-3"
                                            >
                                                <div className="flex items-center justify-between gap-3">
                                                    <p className="truncate text-sm font-medium">{item.filePath}</p>
                                                    <div className="flex gap-2">
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            className="border-transparent px-2"
                                                            onClick={() => props.onOpenFile(item.filePath)}
                                                        >
                                                            Open file
                                                        </Button>
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            className="border-transparent px-2"
                                                            disabled={!item.diffAvailable}
                                                            onClick={() => props.onOpenDiff(item.filePath)}
                                                        >
                                                            Open diff
                                                        </Button>
                                                    </div>
                                                </div>
                                                <p className="mt-2 text-xs leading-6 text-[var(--te-muted)]">
                                                    {item.summary}
                                                </p>
                                                {preview.lines.length > 0 ? (
                                                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-[12px] border border-[var(--te-border)] bg-[var(--te-surface)] p-3 text-[11px] leading-5">
                                                        {preview.lines.join("\n")}
                                                    </pre>
                                                ) : null}
                                                <p className="mt-2 text-[11px] text-[var(--te-muted)]">
                                                    {preview.summary}
                                                </p>
                                            </div>
                                        );
                                    })}
                                </div>
                            </DetailsSection>
                        ) : null}
                        {props.message.details.verifySummary.length > 0 ? (
                            <DetailsSection title="Verification">
                                <ul className="space-y-2 text-xs leading-6 text-[var(--te-muted)]">
                                    {props.message.details.verifySummary.map((item, index) => {
                                        return <li key={`${props.message.id}_verify_${index}`}>{item}</li>;
                                    })}
                                </ul>
                            </DetailsSection>
                        ) : null}
                        {props.message.details.rawTranscriptSnippet ? (
                            <DetailsSection title="Raw trace snippet">
                                <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-[var(--te-muted)]">
                                    {props.message.details.rawTranscriptSnippet}
                                </pre>
                            </DetailsSection>
                        ) : null}
                    </div>
                ) : null}
            </article>
        </div>
    );
}

function DetailsSection(props: { title: string; children: React.ReactNode }) {
    return (
        <section className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--te-muted)]">
                {props.title}
            </p>
            {props.children}
        </section>
    );
}

function resolveMessageBadgeLabel(
    kind: CodeConversationMessageViewModel["kind"],
    runStatus: CodeConversationRunViewModel["status"],
): string {
    if (kind === "user_turn") {
        return "prompt";
    }
    if (kind === "assistant_working") {
        return runStatus === "running" ? "working" : "details";
    }
    if (kind === "assistant_error") {
        return "error";
    }
    if (runStatus === "applied") {
        return "applied";
    }
    if (runStatus === "ready_to_apply") {
        return "ready";
    }
    return "result";
}

function resolveMessageBadgeVariant(
    kind: CodeConversationMessageViewModel["kind"],
    runStatus: CodeConversationRunViewModel["status"],
): "accent" | "subtle" | "warning" | undefined {
    if (kind === "assistant_error") {
        return "warning";
    }
    if (kind === "assistant_result") {
        return runStatus === "ready_to_apply" ? "accent" : "subtle";
    }
    return "subtle";
}

function hasTurnDetails(message: CodeConversationMessageViewModel): boolean {
    const details = message.details;
    if (!details) {
        return false;
    }
    return (
        details.reasoningSummary !== null ||
        details.toolTrace.length > 0 ||
        details.fileChanges.length > 0 ||
        details.verifySummary.length > 0 ||
        details.rawTranscriptSnippet !== null
    );
}

function buildDiffPreview(file: AssistantFileChangeViewModel): {
    summary: string;
    lines: string[];
} {
    if (file.baseSource !== "workspace_live" || file.baseContent === null) {
        const contentLines = file.content
            .split("\n")
            .filter((line, index, lines) => !(index === lines.length - 1 && line === ""))
            .slice(0, 5)
            .map((line) => `+ ${line}`);
        return {
            summary: "Draft text preview only.",
            lines: contentLines,
        };
    }
    const parts = diffLines(file.baseContent, file.content);
    const lines: string[] = [];
    let added = 0;
    let removed = 0;
    for (const part of parts) {
        if (!part.added && !part.removed) {
            continue;
        }
        const prefix = part.added ? "+" : "-";
        const rawLines = part.value
            .split("\n")
            .filter((line, index, values) => !(index === values.length - 1 && line === ""));
        if (part.added) {
            added += rawLines.length;
        }
        if (part.removed) {
            removed += rawLines.length;
        }
        for (const line of rawLines) {
            if (lines.length >= 5) {
                break;
            }
            lines.push(`${prefix} ${line}`);
        }
        if (lines.length >= 5) {
            break;
        }
    }
    return {
        summary: `${added > 0 ? `+${added}` : "+0"} ${removed > 0 ? `-${removed}` : "-0"}`,
        lines,
    };
}
