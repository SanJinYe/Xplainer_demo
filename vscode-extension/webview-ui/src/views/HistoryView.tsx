import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { CollapsibleCard } from "../components/ui/collapsible-card";
import { DraftFilesPanel } from "../components/ui/draft-files-panel";
import { StepList } from "../components/ui/step-list";
import { useWebviewState } from "../context/WebviewStateContext";

const HISTORY_STATUS_OPTIONS = [
    "all",
    "ready_to_apply",
    "applied",
    "failed",
    "cancelled",
    "applied_event_pending",
    "applied_without_events",
] as const;

export function HistoryView() {
    const { state, actions } = useWebviewState();
    const data = state.codeState;

    if (!data) {
        return (
            <div className="flex h-full items-center justify-center p-6">
                <p className="text-sm text-[var(--te-muted)]">Waiting for history state.</p>
            </div>
        );
    }

    const detail = data.historyDetail;

    return (
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
            <section className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-bg)] px-4 py-4">
                <div className="grid gap-3 xl:grid-cols-[180px_1fr_auto]">
                    <label className="space-y-2 text-sm">
                        <span className="font-medium">Status</span>
                        <select
                            className="w-full rounded-[16px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-2 outline-none"
                            value={data.historyFilters.status}
                            onChange={(event) => actions.send({
                                type: "setHistoryStatusFilter",
                                status: event.currentTarget.value as typeof HISTORY_STATUS_OPTIONS[number],
                            })}
                        >
                            {HISTORY_STATUS_OPTIONS.map((item) => {
                                return (
                                    <option key={item} value={item}>
                                        {item}
                                    </option>
                                );
                            })}
                        </select>
                    </label>
                    <label className="space-y-2 text-sm">
                        <span className="font-medium">Target Query</span>
                        <input
                            className="w-full rounded-[16px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-2 outline-none"
                            value={data.historyFilters.targetQuery}
                            placeholder="Filter task history by target path"
                            onChange={(event) => actions.send({
                                type: "setHistoryTargetQuery",
                                query: event.currentTarget.value,
                            })}
                        />
                    </label>
                    <div className="flex items-end">
                        <Button
                            variant="ghost"
                            disabled={!data.historyPage.hasMore}
                            onClick={() => actions.send({ type: "loadMoreHistory" })}
                        >
                            Load More
                        </Button>
                    </div>
                </div>
                {data.historyFilters.targetSuggestions.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                            variant={data.historyFilters.targetFilePath === null ? "primary" : "ghost"}
                            size="sm"
                            onClick={() => actions.send({ type: "setHistoryTargetSelection", targetFilePath: null })}
                        >
                            All targets
                        </Button>
                        {data.historyFilters.targetSuggestions.map((item) => {
                            return (
                                <Button
                                    key={item}
                                    variant={data.historyFilters.targetFilePath === item ? "primary" : "ghost"}
                                    size="sm"
                                    onClick={() => actions.send({ type: "setHistoryTargetSelection", targetFilePath: item })}
                                >
                                    {item}
                                </Button>
                            );
                        })}
                    </div>
                ) : null}
                {data.historyError ? (
                    <p className="mt-3 text-sm text-[var(--te-danger)]">{data.historyError}</p>
                ) : null}
                {data.historyNotice ? (
                    <p className="mt-3 text-sm text-[var(--te-muted)]">{data.historyNotice}</p>
                ) : null}
            </section>

            <div className="grid min-h-0 gap-4 xl:grid-cols-[0.95fr_1.05fr]">
                <section className="space-y-3">
                    {data.historyItems.length === 0 ? (
                        <div className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] px-4 py-6 text-sm text-[var(--te-muted)]">
                            No task history yet.
                        </div>
                    ) : (
                        data.historyItems.map((item) => {
                            return (
                                <button
                                    key={item.taskId}
                                    type="button"
                                    className="w-full rounded-[18px] border border-[var(--te-border)] bg-[var(--te-bg)] px-4 py-4 text-left"
                                    onClick={() => actions.send({ type: "selectHistoryTask", taskId: item.taskId })}
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <p className="truncate text-sm font-semibold">{item.targetFilePath}</p>
                                            <p className="mt-1 text-xs text-[var(--te-muted)]">{item.userPrompt}</p>
                                        </div>
                                        <div className="flex flex-col items-end gap-2">
                                            <Badge variant={item.selected ? "accent" : "subtle"}>{item.status}</Badge>
                                            <span className="text-[11px] text-[var(--te-muted)]">{item.updatedAt}</span>
                                        </div>
                                    </div>
                                </button>
                            );
                        })
                    )}
                </section>

                <section className="space-y-4">
                    {detail ? (
                        <>
                            <section className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-bg)] px-4 py-4">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h2 className="text-lg font-semibold">{detail.targetFilePath}</h2>
                                            <Badge>{detail.status}</Badge>
                                            <Badge variant="subtle">{detail.launchMode}</Badge>
                                        </div>
                                        <p className="text-sm text-[var(--te-muted)]">{detail.userPrompt}</p>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        <Button
                                            variant="ghost"
                                            onClick={() => actions.send({ type: "reuseHistoryTask", taskId: detail.taskId })}
                                        >
                                            Reuse
                                        </Button>
                                        <Button
                                            variant="primary"
                                            onClick={() => actions.send({ type: "replayHistoryTask", taskId: detail.taskId })}
                                        >
                                            Replay
                                        </Button>
                                    </div>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    {detail.selectedProfileId ? (
                                        <Badge variant="accent">{detail.selectedProfileId}</Badge>
                                    ) : null}
                                    {detail.requestedCapabilities.map((item) => {
                                        return <Badge key={item} variant="subtle">{item}</Badge>;
                                    })}
                                </div>
                                {detail.lastError ? (
                                    <p className="mt-4 text-sm text-[var(--te-danger)]">{detail.lastError}</p>
                                ) : null}
                            </section>

                            <CollapsibleCard
                                id="history.steps"
                                title="Structured Steps"
                                description="History detail keeps step semantics instead of only flattened transcript."
                                open={!state.ui.collapsedPanels["history.steps"]}
                                onOpenChange={(open) => actions.setPanelCollapsed("history.steps", !open)}
                            >
                                <StepList steps={detail.steps} />
                            </CollapsibleCard>

                            <CollapsibleCard
                                id="history.draft"
                                title="Draft Files"
                                description="History uses text-only draft rendering when no reliable base content exists."
                                open={!state.ui.collapsedPanels["history.draft"]}
                                onOpenChange={(open) => actions.setPanelCollapsed("history.draft", !open)}
                            >
                                <DraftFilesPanel
                                    draftFiles={detail.draftFiles}
                                    onOpenFile={(path) => actions.send({ type: "openWorkspaceFile", path })}
                                />
                            </CollapsibleCard>

                            <CollapsibleCard
                                id="history.details"
                                title="Technical Detail"
                                description="Transcript, model output, and apply metadata remain available."
                                open={!state.ui.collapsedPanels["history.details"]}
                                onOpenChange={(open) => actions.setPanelCollapsed("history.details", !open)}
                            >
                                <div className="space-y-4">
                                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                                        {detail.transcriptText || "No transcript."}
                                    </pre>
                                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                                        {detail.modelOutputText || "No model output."}
                                    </pre>
                                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-2xl border border-[var(--te-border)] bg-[var(--te-bg)] p-3 text-xs leading-6">
                                        {detail.draftText || "No draft text."}
                                    </pre>
                                </div>
                            </CollapsibleCard>
                        </>
                    ) : (
                        <div className="rounded-[18px] border border-[var(--te-border)] bg-[var(--te-surface)] px-4 py-6 text-sm text-[var(--te-muted)]">
                            Select a task to inspect its history detail.
                        </div>
                    )}
                </section>
            </div>
        </div>
    );
}
