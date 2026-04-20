import { useEffect, useMemo, useRef, useState } from "react";

import type {
    CodeConversationMessageViewModel,
    CodeConversationRunViewModel,
    CodePickerKind,
    CodePickerViewModel,
} from "../../../src/types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Chip } from "../components/ui/chip";
import { useWebviewState } from "../context/WebviewStateContext";
import { TaskCardList } from "./code/TaskCardList";

const AUTO_SCROLL_THRESHOLD = 24;

export function CodeWorkbenchView() {
    const { state, actions } = useWebviewState();
    const data = state.codeState;
    const conversationRef = useRef<HTMLDivElement | null>(null);
    const [autoFollow, setAutoFollow] = useState(true);

    const latestRun = useMemo(() => data?.conversation.runs.at(-1) ?? null, [data]);
    const latestReadyResult = useMemo(() => findLatestReadyResult(latestRun), [latestRun]);
    const showResultActions =
        data?.status === "ready_to_apply" &&
        latestReadyResult !== null &&
        state.ui.dismissedResultMessageId !== latestReadyResult.id;

    useEffect(() => {
        if (!conversationRef.current || !autoFollow) {
            return;
        }
        conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
    }, [autoFollow, data?.conversation.runs, data?.status]);

    if (!data) {
        return (
            <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-6">
                <p className="text-sm text-[var(--te-muted)]">Waiting for code state.</p>
            </div>
        );
    }

    const hasRuns = data.conversation.runs.length > 0;
    const hasHintTarget = data.conversation.composerHintTarget !== null && data.conversation.composerHintTarget !== undefined;

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            <section className="border-b border-[var(--te-border)] px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <Badge variant="subtle">{data.status}</Badge>
                        <Badge variant="subtle">{data.codeProfile.label}</Badge>
                        {latestRun?.launchMode === "replay" && latestRun.sourceTaskId ? (
                            <Badge variant="warning">{`replay:${latestRun.sourceTaskId}`}</Badge>
                        ) : null}
                        <span className="truncate text-xs text-[var(--te-muted)]">
                            {data.message}
                        </span>
                    </div>
                    <div className="flex gap-2">
                        <Button
                            variant="ghost"
                            size="sm"
                            className="border-transparent px-2"
                            disabled={!hasRuns || data.status === "running" || data.status === "applying"}
                            onClick={() => actions.send({ type: "clearCodeConversation" })}
                        >
                            Clear
                        </Button>
                    </div>
                </div>
            </section>

            <div className="relative min-h-0 flex-1">
                <div
                    ref={conversationRef}
                    className="h-full min-h-0 overflow-y-auto px-3 py-3"
                    onScroll={(event) => {
                        const element = event.currentTarget;
                        const distanceToBottom =
                            element.scrollHeight - element.scrollTop - element.clientHeight;
                        setAutoFollow(distanceToBottom <= AUTO_SCROLL_THRESHOLD);
                    }}
                >
                    {hasRuns ? (
                        <TaskCardList
                            runs={data.conversation.runs}
                            onOpenDiff={(path) => actions.send({ type: "openDiffView", path })}
                            onOpenFile={(path) => actions.send({ type: "openWorkspaceFile", path })}
                        />
                    ) : (
                        <IdleConversation
                            hintTarget={data.conversation.composerHintTarget ?? null}
                            message={data.message}
                        />
                    )}
                    {!hasRuns && data.conversation.recentTasks.length > 0 ? (
                        <RecentTasks
                            items={data.conversation.recentTasks}
                            onSelect={(taskId) => {
                                actions.setActiveView("history");
                                actions.send({ type: "selectHistoryTask", taskId });
                            }}
                        />
                    ) : null}
                </div>
                {data.status === "running" && !autoFollow ? (
                    <div className="pointer-events-none absolute bottom-3 right-3">
                        <Button
                            variant="subtle"
                            size="sm"
                            className="pointer-events-auto shadow-none"
                            onClick={() => {
                                setAutoFollow(true);
                                if (conversationRef.current) {
                                    conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
                                }
                            }}
                        >
                            Jump to latest
                        </Button>
                    </div>
                ) : null}
            </div>

            {showResultActions && latestReadyResult ? (
                <div className="border-t border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--te-muted)]">
                                Ready to apply
                            </p>
                            <p className="text-xs text-[var(--te-muted)]">{latestReadyResult.text}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button
                                variant="primary"
                                disabled={!data.canApply}
                                onClick={() => actions.send({ type: "applyTask" })}
                            >
                                Apply
                            </Button>
                            <Button variant="ghost" onClick={() => actions.dismissDraftReady()}>
                                Dismiss
                            </Button>
                        </div>
                    </div>
                </div>
            ) : null}

            <section className="border-t border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-3">
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <FileChip
                            label={hasHintTarget ? `Hint: ${data.conversation.composerHintTarget}` : "Hint: follow active editor"}
                            tone={hasHintTarget ? "accent" : "default"}
                            onClick={() => actions.send({ type: "setCodePickerOpen", kind: "target", open: true })}
                        />
                        {data.contextFiles.map((item) => {
                            return (
                                <FileChip
                                    key={`context-${item}`}
                                    label={item}
                                    onClick={() => actions.send({ type: "openWorkspaceFile", path: item })}
                                    onRemove={() => actions.send({ type: "removeSelectedFile", kind: "context", path: item })}
                                />
                            );
                        })}
                        {data.editableFiles.map((item) => {
                            return (
                                <FileChip
                                    key={`editable-${item}`}
                                    label={`${item} (editable)`}
                                    tone="warning"
                                    onClick={() => actions.send({ type: "openWorkspaceFile", path: item })}
                                    onRemove={() => actions.send({ type: "removeSelectedFile", kind: "editable", path: item })}
                                />
                            );
                        })}
                        <Button
                            variant="ghost"
                            size="sm"
                            className="border-transparent px-2"
                            onClick={() => actions.send({ type: "setCodePickerOpen", kind: "context", open: true })}
                        >
                            + Context
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="border-transparent px-2"
                            onClick={() => actions.send({ type: "setCodePickerOpen", kind: "editable", open: true })}
                        >
                            + Editable
                        </Button>
                        {data.targetSelectionMode === "explicit" ? (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="border-transparent px-2"
                                onClick={() => actions.send({ type: "useActiveTargetFile" })}
                            >
                                Follow editor
                            </Button>
                        ) : null}
                    </div>

                    <PickerSection
                        kind="target"
                        picker={data.targetPicker}
                        singleSelect
                        onSearch={(search) => actions.send({ type: "setCodePickerSearch", kind: "target", search })}
                        onSingleSelect={(path) => actions.send({ type: "setTargetPickerSelection", path })}
                        onApply={() => actions.send({ type: "applyCodePickerSelection", kind: "target" })}
                        onCancel={() => actions.send({ type: "cancelCodePickerSelection", kind: "target" })}
                    />
                    <PickerSection
                        kind="context"
                        picker={data.contextPicker}
                        onSearch={(search) => actions.send({ type: "setCodePickerSearch", kind: "context", search })}
                        onToggle={(path, selected) =>
                            actions.send({
                                type: "toggleCodePickerSelection",
                                kind: "context",
                                path,
                                selected,
                            })}
                        onApply={() => actions.send({ type: "applyCodePickerSelection", kind: "context" })}
                        onCancel={() => actions.send({ type: "cancelCodePickerSelection", kind: "context" })}
                    />
                    <PickerSection
                        kind="editable"
                        picker={data.editablePicker}
                        onSearch={(search) => actions.send({ type: "setCodePickerSearch", kind: "editable", search })}
                        onToggle={(path, selected) =>
                            actions.send({
                                type: "toggleCodePickerSelection",
                                kind: "editable",
                                path,
                                selected,
                            })}
                        onApply={() => actions.send({ type: "applyCodePickerSelection", kind: "editable" })}
                        onCancel={() => actions.send({ type: "cancelCodePickerSelection", kind: "editable" })}
                    />

                    <textarea
                        className="min-h-[96px] w-full rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] px-4 py-3 text-sm text-[var(--te-foreground)] outline-none"
                        value={state.persisted.promptDraft}
                        disabled={data.status === "running" || data.status === "applying"}
                        onChange={(event) => actions.setPromptDraft(event.currentTarget.value)}
                        placeholder="Describe the code change you want."
                    />
                    <div className="flex flex-wrap justify-end gap-2">
                        <Button
                            variant="ghost"
                            disabled={!data.canCancel}
                            onClick={() => actions.send({ type: "cancelTask" })}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            disabled={!data.canRun}
                            onClick={() => actions.send({ type: "runTask", prompt: state.persisted.promptDraft })}
                        >
                            Run
                        </Button>
                    </div>
                </div>
            </section>
        </div>
    );
}

function IdleConversation(props: { hintTarget: string | null; message: string | null }) {
    return (
        <div className="space-y-3 py-6">
            <div className="max-w-[420px] rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] px-4 py-3">
                <p className="text-sm font-medium">Start with the prompt below.</p>
                <p className="mt-2 text-xs leading-6 text-[var(--te-muted)]">
                    {props.message ?? "The assistant will gather workflow details and collapse them into the run."}
                </p>
                <p className="mt-2 text-[11px] text-[var(--te-muted)]">
                    {props.hintTarget ? `Current hint target: ${props.hintTarget}` : "No active file hint yet."}
                </p>
            </div>
        </div>
    );
}

function RecentTasks(props: {
    items: Array<{
        taskId: string;
        targetFilePath: string;
        userPrompt: string;
        status: string;
        updatedAt: string;
    }>;
    onSelect: (taskId: string) => void;
}) {
    return (
        <section className="mt-4 space-y-2">
            <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--te-muted)]">
                    Recent tasks
                </p>
                <p className="text-[11px] text-[var(--te-muted)]">Open History for full detail.</p>
            </div>
            <div className="space-y-2">
                {props.items.map((item) => {
                    return (
                        <button
                            key={item.taskId}
                            type="button"
                            className="w-full rounded-[14px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-3 text-left transition-colors hover:border-[var(--te-border-strong)]"
                            onClick={() => props.onSelect(item.taskId)}
                        >
                            <div className="flex items-center justify-between gap-3">
                                <p className="truncate text-sm font-medium">{item.targetFilePath}</p>
                                <Badge variant="subtle">{item.status}</Badge>
                            </div>
                            <p className="mt-2 line-clamp-2 text-xs text-[var(--te-muted)]">{item.userPrompt}</p>
                            <p className="mt-2 text-[11px] text-[var(--te-muted)]">{item.updatedAt}</p>
                        </button>
                    );
                })}
            </div>
        </section>
    );
}

function FileChip(props: {
    label: string;
    tone?: "default" | "accent" | "warning";
    onClick?: () => void;
    onRemove?: () => void;
}) {
    return (
        <Chip
            className={props.tone === "accent"
                ? "border-[var(--te-border-strong)] bg-[var(--te-subtle-accent)]"
                : props.tone === "warning"
                    ? "border-[var(--te-warning)]"
                    : undefined}
        >
            <button type="button" className="truncate text-left" onClick={props.onClick}>
                {props.label}
            </button>
            {props.onRemove ? (
                <button
                    type="button"
                    className="text-[var(--te-muted)]"
                    aria-label={`Remove ${props.label}`}
                    onClick={props.onRemove}
                >
                    x
                </button>
            ) : null}
        </Chip>
    );
}

function PickerSection(props: {
    kind: CodePickerKind;
    picker: CodePickerViewModel;
    onSearch: (search: string) => void;
    onApply: () => void;
    onCancel: () => void;
    onSingleSelect?: (path: string) => void;
    onToggle?: (path: string, selected: boolean) => void;
    singleSelect?: boolean;
}) {
    if (!props.picker.open) {
        return null;
    }
    return (
        <Card className="border-[var(--te-border-strong)] bg-[var(--te-surface)] p-3">
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">{`Select ${props.kind} files`}</p>
                <Badge variant="accent">{props.picker.candidates.length}</Badge>
            </div>
            <input
                className="mt-3 w-full rounded-[14px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2 text-sm outline-none"
                value={props.picker.search}
                placeholder="Filter workspace files"
                onChange={(event) => props.onSearch(event.currentTarget.value)}
            />
            <div className="mt-3 max-h-56 space-y-2 overflow-y-auto">
                {props.picker.candidates.length === 0 ? (
                    <p className="text-sm text-[var(--te-muted)]">No matching files.</p>
                ) : (
                    props.picker.candidates.map((item) => {
                        return (
                            <label
                                key={item.path}
                                className="flex items-center gap-3 rounded-[14px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2 text-sm"
                            >
                                <input
                                    type={props.singleSelect ? "radio" : "checkbox"}
                                    checked={item.selected}
                                    name={props.singleSelect ? `${props.kind}-picker` : undefined}
                                    onChange={(event) => {
                                        if (props.singleSelect) {
                                            props.onSingleSelect?.(item.path);
                                            return;
                                        }
                                        props.onToggle?.(item.path, event.currentTarget.checked);
                                    }}
                                />
                                <span className="min-w-0 truncate">{item.path}</span>
                            </label>
                        );
                    })
                )}
            </div>
            <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={props.onCancel}>Cancel</Button>
                <Button variant="primary" onClick={props.onApply}>Apply</Button>
            </div>
        </Card>
    );
}

function findLatestReadyResult(
    run: CodeConversationRunViewModel | null,
): CodeConversationMessageViewModel | null {
    if (!run || run.status !== "ready_to_apply") {
        return null;
    }
    for (let index = run.messages.length - 1; index >= 0; index -= 1) {
        const message = run.messages[index];
        if (message.kind === "assistant_result") {
            return message;
        }
    }
    return null;
}
