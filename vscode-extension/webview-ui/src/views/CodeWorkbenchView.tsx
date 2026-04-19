import type { CodePickerKind, CodePickerViewModel, DraftFileViewModel } from "../../../src/types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { CollapsibleCard } from "../components/ui/collapsible-card";
import { DraftFilesPanel } from "../components/ui/draft-files-panel";
import { useWebviewState } from "../context/WebviewStateContext";

export function CodeWorkbenchView() {
    const { state, actions } = useWebviewState();
    const data = state.codeState;

    if (!data) {
        return (
            <div className="flex h-full items-center justify-center p-6">
                <p className="text-sm text-[var(--te-muted)]">Waiting for code state.</p>
            </div>
        );
    }

    return (
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
            <section className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-bg)] px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-2">
                        <p className="text-[11px] uppercase tracking-[0.16em] text-[var(--te-muted)]">Target</p>
                        <div className="flex flex-wrap items-center gap-2">
                            <h2 className="text-xl font-semibold">
                                {data.filePath ?? "No target selected"}
                            </h2>
                            <Badge variant={data.targetSelectionMode === "explicit" ? "accent" : "subtle"}>
                                {data.targetSelectionMode}
                            </Badge>
                        </div>
                        <p className="text-sm text-[var(--te-muted)]">{data.message}</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button
                            variant="ghost"
                            onClick={() => actions.send({ type: "setCodePickerOpen", kind: "target", open: true })}
                        >
                            Change Target
                        </Button>
                        <Button variant="ghost" onClick={() => actions.send({ type: "useActiveTargetFile" })}>
                            Use Active File
                        </Button>
                        {data.explainEntity.canUseAsTarget ? (
                            <Button
                                variant="subtle"
                                onClick={() => actions.send({ type: "useExplainFileAsTarget" })}
                            >
                                Use Explain File
                            </Button>
                        ) : null}
                    </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                    <Badge>{data.status}</Badge>
                    <Badge variant="accent">{data.codeProfile.label}</Badge>
                    <Badge variant="subtle">{data.explainProfile.label}</Badge>
                    {data.capabilitySummary.available.map((item) => {
                        return <Badge key={item.key} variant="subtle">{item.key}</Badge>;
                    })}
                </div>
                <div className="mt-4 grid gap-3 xl:grid-cols-3">
                    <SelectionBlock
                        title="Context Files"
                        items={data.contextFiles}
                        onOpenPicker={() => actions.send({ type: "setCodePickerOpen", kind: "context", open: true })}
                        onOpenFile={(path) => actions.send({ type: "openWorkspaceFile", path })}
                        onRemove={(path) => actions.send({ type: "removeSelectedFile", kind: "context", path })}
                    />
                    <SelectionBlock
                        title="Editable Files"
                        items={data.editableFiles}
                        onOpenPicker={() => actions.send({ type: "setCodePickerOpen", kind: "editable", open: true })}
                        onOpenFile={(path) => actions.send({ type: "openWorkspaceFile", path })}
                        onRemove={(path) => actions.send({ type: "removeSelectedFile", kind: "editable", path })}
                    />
                    <div className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] p-4">
                        <p className="text-sm font-semibold">Profiles</p>
                        <p className="mt-2 text-sm">{data.codeProfile.label}</p>
                        <p className="text-xs text-[var(--te-muted)]">
                            {formatProfileMeta(data.codeProfile.backend, data.codeProfile.model, data.codeProfile.reason)}
                        </p>
                        <div className="mt-4 flex flex-wrap gap-2">
                            <Button variant="ghost" size="sm" onClick={() => actions.send({ type: "selectCodeProfile" })}>
                                Select Code
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => actions.send({ type: "selectExplainProfile" })}>
                                Select Explain
                            </Button>
                        </div>
                    </div>
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
                    onToggle={(path, selected) => actions.send({
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
                    onToggle={(path, selected) => actions.send({
                        type: "toggleCodePickerSelection",
                        kind: "editable",
                        path,
                        selected,
                    })}
                    onApply={() => actions.send({ type: "applyCodePickerSelection", kind: "editable" })}
                    onCancel={() => actions.send({ type: "cancelCodePickerSelection", kind: "editable" })}
                />
            </section>

            <section className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-bg)] px-4 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <p className="text-sm font-semibold">Prompt Composer</p>
                        <p className="text-xs text-[var(--te-muted)]">
                            Host owns execution; Webview only edits the local draft prompt.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button
                            variant="primary"
                            disabled={!data.canRun}
                            onClick={() => actions.send({ type: "runTask", prompt: state.ui.promptDraft })}
                        >
                            Run
                        </Button>
                        <Button
                            variant="ghost"
                            disabled={!data.canCancel}
                            onClick={() => actions.send({ type: "cancelTask" })}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="subtle"
                            disabled={!data.canApply}
                            onClick={() => actions.send({ type: "applyTask" })}
                        >
                            Apply
                        </Button>
                    </div>
                </div>
                <textarea
                    className="mt-4 min-h-[140px] w-full rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] px-4 py-3 text-sm text-[var(--te-foreground)] outline-none"
                    value={state.ui.promptDraft}
                    onChange={(event) => actions.setPromptDraft(event.currentTarget.value)}
                    placeholder="Describe the change you want the coding runtime to make."
                />
            </section>

            <div className="grid gap-4 xl:grid-cols-2">
                <CollapsibleCard
                    id="code.transcript"
                    title="Transcript"
                    description="Session transcript emitted by the coding runtime."
                    open={!state.ui.collapsedPanels["code.transcript"]}
                    onOpenChange={(open) => actions.setPanelCollapsed("code.transcript", !open)}
                >
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                        {data.transcriptText || "No transcript yet."}
                    </pre>
                </CollapsibleCard>

                <CollapsibleCard
                    id="code.model"
                    title="Model Output"
                    description="Raw model output stays visible as technical detail."
                    open={!state.ui.collapsedPanels["code.model"]}
                    onOpenChange={(open) => actions.setPanelCollapsed("code.model", !open)}
                >
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                        {data.modelOutputText || "No model output yet."}
                    </pre>
                </CollapsibleCard>
            </div>

            <CollapsibleCard
                id="code.draft"
                title="Verified Draft"
                description="Draft text plus file-level change view."
                open={!state.ui.collapsedPanels["code.draft"]}
                onOpenChange={(open) => actions.setPanelCollapsed("code.draft", !open)}
            >
                <div className="space-y-4">
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                        {data.draftText || "No verified draft yet."}
                    </pre>
                    <DraftFilesPanel
                        draftFiles={data.draftFiles}
                        onOpenFile={(path) => actions.send({ type: "openWorkspaceFile", path })}
                    />
                </div>
            </CollapsibleCard>
        </div>
    );
}

function SelectionBlock(props: {
    title: string;
    items: string[];
    onOpenPicker: () => void;
    onOpenFile: (path: string) => void;
    onRemove: (path: string) => void;
}) {
    return (
        <div className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] p-4">
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">{props.title}</p>
                <Button variant="ghost" size="sm" onClick={props.onOpenPicker}>
                    Manage
                </Button>
            </div>
            {props.items.length === 0 ? (
                <p className="mt-3 text-sm text-[var(--te-muted)]">No files selected.</p>
            ) : (
                <div className="mt-3 space-y-2">
                    {props.items.map((item) => {
                        return (
                            <div
                                key={item}
                                className="flex items-center justify-between gap-2 rounded-[14px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2"
                            >
                                <button
                                    type="button"
                                    className="min-w-0 flex-1 truncate text-left text-sm"
                                    onClick={() => props.onOpenFile(item)}
                                >
                                    {item}
                                </button>
                                <Button variant="ghost" size="sm" onClick={() => props.onRemove(item)}>
                                    Remove
                                </Button>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
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
        <div className="mt-4 rounded-[18px] border border-[var(--te-border-strong)] bg-[var(--te-subtle-accent)] p-4">
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">Select {props.kind} files</p>
                <Badge variant="accent">{props.picker.candidates.length} candidates</Badge>
            </div>
            <input
                className="mt-3 w-full rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2 text-sm outline-none"
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
        </div>
    );
}

function formatProfileMeta(
    backend: string | null,
    model: string | null,
    reason: string | null,
): string {
    const parts = [backend, model].filter(Boolean);
    if (parts.length === 0) {
        return reason ?? "Profile ready.";
    }
    return parts.join(" / ");
}
