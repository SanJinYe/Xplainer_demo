import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { CollapsibleCard } from "../components/ui/collapsible-card";
import { useWebviewState } from "../context/WebviewStateContext";

export function ExplainView() {
    const { state, actions } = useWebviewState();
    const explainState = state.explainState;

    if (explainState.type === "state:empty") {
        return (
            <div className="flex min-h-full items-center justify-center p-6">
                <div className="max-w-md space-y-4 text-center">
                    <p className="text-lg font-semibold">Explain</p>
                    <p className="text-sm text-[var(--te-muted)]">{explainState.message}</p>
                    <Button variant="subtle" onClick={() => actions.send({ type: "refresh" })}>
                        Refresh
                    </Button>
                </div>
            </div>
        );
    }

    if (explainState.type === "state:loading") {
        return (
            <div className="flex min-h-full items-center justify-center p-6">
                <div className="space-y-2 text-center">
                    <p className="text-lg font-semibold">Loading explanation</p>
                    <p className="text-sm text-[var(--te-muted)]">
                        {explainState.label ? `Loading ${explainState.label}...` : "Loading..."}
                    </p>
                </div>
            </div>
        );
    }

    if (explainState.type === "state:error") {
        return (
            <div className="flex min-h-full items-center justify-center p-6">
                <div className="max-w-md space-y-4 text-center">
                    <p className="text-lg font-semibold">Explain failed</p>
                    <p className="text-sm text-[var(--te-muted)]">
                        {formatExplainError(explainState.error, explainState.baseUrl)}
                    </p>
                    <Button variant="primary" onClick={() => actions.send({ type: "refresh" })}>
                        Retry
                    </Button>
                </div>
            </div>
        );
    }

    const data = explainState.data;
    return (
        <div className="flex min-h-full flex-col gap-3 px-3 py-3">
            <section className="rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                            <h2 className="text-lg font-semibold">{data.entityName}</h2>
                            <Badge>{data.entityType}</Badge>
                            {data.profile ? <Badge variant="accent">{data.profile.label}</Badge> : null}
                        </div>
                        <p className="text-sm text-[var(--te-muted)]">
                            {data.lineStart && data.lineEnd
                                ? `${data.filePath}:${data.lineStart}-${data.lineEnd}`
                                : data.filePath}
                        </p>
                        {data.signature ? (
                            <pre className="whitespace-pre-wrap rounded-[16px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-2 text-xs">
                                {data.signature}
                            </pre>
                        ) : null}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button
                            variant="ghost"
                            onClick={() => actions.send({ type: "openWorkspaceFile", path: data.filePath })}
                        >
                            Open File
                        </Button>
                        <Button variant="subtle" onClick={() => actions.send({ type: "refresh" })}>
                            Refresh
                        </Button>
                    </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                    <Badge variant="subtle">{`${data.eventCount} linked events`}</Badge>
                    {data.disclaimer ? <Badge variant="warning">{data.disclaimer}</Badge> : null}
                    {data.summaryPending ? <Badge variant="accent">summary pending</Badge> : null}
                </div>
            </section>

            <CollapsibleCard
                id="explain.summary"
                title="Summary"
                description="Structured explanation with profile-aware evidence."
                open={!state.persisted.collapsedPanels["explain.summary"]}
                onOpenChange={(open) => actions.setPanelCollapsed("explain.summary", !open)}
            >
                <div className="space-y-4">
                    {data.summary ? <p className="text-base font-medium">{data.summary}</p> : null}
                    {data.detailedExplanation ? (
                        <p className="whitespace-pre-wrap text-sm leading-7">
                            {data.detailedExplanation}
                        </p>
                    ) : (
                        <p className="text-sm text-[var(--te-muted)]">
                            No detailed explanation yet.
                        </p>
                    )}
                    {data.streamError ? (
                        <p className="text-sm text-[var(--te-danger)]">{data.streamError}</p>
                    ) : null}
                </div>
            </CollapsibleCard>

            <CollapsibleCard
                id="explain.relations"
                title="Relation Context"
                description="Callers, callees, related entities, and global impact."
                open={!state.persisted.collapsedPanels["explain.relations"]}
                onOpenChange={(open) => actions.setPanelCollapsed("explain.relations", !open)}
            >
                <div className="grid gap-3 xl:grid-cols-2">
                    <RelationList
                        title="Callers"
                        items={data.callers}
                        onOpenEntity={(entityId) => actions.send({ type: "openRelatedEntity", entityId })}
                    />
                    <RelationList
                        title="Callees"
                        items={data.callees}
                        onOpenEntity={(entityId) => actions.send({ type: "openRelatedEntity", entityId })}
                    />
                    <RelationList
                        title="Related"
                        items={data.relatedEntities}
                        onOpenEntity={(entityId) => actions.send({ type: "openRelatedEntity", entityId })}
                    />
                    <div className="space-y-3 rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
                        <div className="flex items-center justify-between gap-2">
                            <h3 className="text-sm font-semibold">Global Impact</h3>
                            {data.globalImpactSummary ? <Badge variant="accent">summary</Badge> : null}
                        </div>
                        {data.globalImpactSummary ? (
                            <p className="text-sm text-[var(--te-muted)]">{data.globalImpactSummary}</p>
                        ) : null}
                        {data.globalImpactPaths.length === 0 ? (
                            <p className="text-sm text-[var(--te-muted)]">{data.globalImpactEmptyText}</p>
                        ) : (
                            <div className="space-y-3">
                                {data.globalImpactPaths.map((item) => {
                                    return (
                                        <button
                                            key={`${item.direction}-${item.terminalEntityId}`}
                                            type="button"
                                            className="w-full rounded-[14px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-3 text-left"
                                            onClick={() => actions.send({
                                                type: "openRelatedEntity",
                                                entityId: item.terminalEntityId,
                                            })}
                                        >
                                            <div className="flex items-center gap-2">
                                                <Badge>{item.direction}</Badge>
                                                <span className="text-sm font-medium">{item.terminalLabel}</span>
                                            </div>
                                            <p className="mt-2 text-xs text-[var(--te-muted)]">{item.qualifiedPath}</p>
                                            <p className="mt-1 text-xs text-[var(--te-muted)]">{item.costLabel}</p>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>
            </CollapsibleCard>

            <CollapsibleCard
                id="explain.reviewHints"
                title="Review Hints"
                description="Explain, impact, and review cues from the wrapper trace."
                open={!state.persisted.collapsedPanels["explain.reviewHints"]}
                onOpenChange={(open) => actions.setPanelCollapsed("explain.reviewHints", !open)}
            >
                {data.reviewHints.length === 0 ? (
                    <p className="text-sm text-[var(--te-muted)]">No review hints yet.</p>
                ) : (
                    <div className="grid gap-3 xl:grid-cols-3">
                        {data.reviewHints.map((hint) => {
                            return (
                                <div
                                    key={hint.id}
                                    className="min-w-0 rounded-[12px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3"
                                >
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Badge variant={hint.severity === "warning" ? "warning" : hint.severity === "success" ? "accent" : "subtle"}>
                                            {hint.category}
                                        </Badge>
                                        <span className="text-sm font-semibold">{hint.title}</span>
                                    </div>
                                    <p className="mt-2 text-sm leading-6 text-[var(--te-muted)]">
                                        {hint.body}
                                    </p>
                                </div>
                            );
                        })}
                    </div>
                )}
            </CollapsibleCard>

            <CollapsibleCard
                id="explain.docs"
                title="Docs And Timeline"
                description="External docs and event timeline stay first-class in TailEvents."
                open={!state.persisted.collapsedPanels["explain.docs"]}
                onOpenChange={(open) => actions.setPanelCollapsed("explain.docs", !open)}
            >
                <div className="grid gap-3 xl:grid-cols-[1.1fr_0.9fr]">
                    <div className="space-y-3 rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
                        <h3 className="text-sm font-semibold">External Docs</h3>
                        {data.externalDocs.length === 0 ? (
                            <p className="text-sm text-[var(--te-muted)]">{data.externalDocsPlaceholder}</p>
                        ) : (
                            data.externalDocs.map((item, index) => {
                                return (
                                    <div
                                        key={`${item.title}-${index}`}
                                        className="rounded-[14px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-3"
                                    >
                                        <p className="text-sm font-medium">{item.title}</p>
                                        <p className="mt-1 text-xs text-[var(--te-muted)]">{item.sourceLabel}</p>
                                        <p className="mt-2 text-sm leading-6">{item.excerpt}</p>
                                    </div>
                                );
                            })
                        )}
                    </div>
                    <div className="space-y-3 rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
                        <h3 className="text-sm font-semibold">Timeline</h3>
                        {data.historyLoading ? (
                            <p className="text-sm text-[var(--te-muted)]">Loading history...</p>
                        ) : data.timeline.length === 0 ? (
                            <p className="text-sm text-[var(--te-muted)]">History unavailable.</p>
                        ) : (
                            data.timeline.map((item) => {
                                return (
                                    <div
                                        key={item.eventId}
                                        className="rounded-[14px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-3"
                                    >
                                        <div className="flex items-center gap-2">
                                            <Badge>{item.actionType}</Badge>
                                            <span className="text-xs text-[var(--te-muted)]">{item.timestamp}</span>
                                        </div>
                                        <p className="mt-2 text-sm">{item.intent}</p>
                                        {item.reasoning ? (
                                            <p className="mt-1 text-xs text-[var(--te-muted)]">{item.reasoning}</p>
                                        ) : null}
                                        {item.renameLabel ? (
                                            <p className="mt-1 text-xs text-[var(--te-muted)]">{item.renameLabel}</p>
                                        ) : null}
                                    </div>
                                );
                            })
                        )}
                    </div>
                </div>
            </CollapsibleCard>
        </div>
    );
}

function RelationList(props: {
    title: string;
    items: Array<{ entityId: string; label: string; relationLabel: string; qualifiedName: string }>;
    onOpenEntity: (entityId: string) => void;
}) {
    return (
        <div className="space-y-3 rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
            <h3 className="text-sm font-semibold">{props.title}</h3>
            {props.items.length === 0 ? (
                <p className="text-sm text-[var(--te-muted)]">No related entities.</p>
            ) : (
                props.items.map((item) => {
                    return (
                        <button
                            key={item.entityId}
                            type="button"
                            className="w-full rounded-[14px] border border-[var(--te-border)] bg-[var(--te-surface)] px-3 py-3 text-left"
                            onClick={() => props.onOpenEntity(item.entityId)}
                        >
                            <p className="text-sm font-medium">{item.label}</p>
                            <p className="mt-1 text-xs text-[var(--te-muted)]">
                                {item.relationLabel} · {item.qualifiedName}
                            </p>
                        </button>
                    );
                })
            )}
        </div>
    );
}

function formatExplainError(error: string, baseUrl: string): string {
    switch (error) {
        case "entity_not_found":
            return "The selected entity was not found in the TailEvents index.";
        case "backend_unavailable":
            return `TailEvents backend is unavailable. Check ${baseUrl}.`;
        case "timeout":
            return "The explanation request timed out.";
        default:
            return "TailEvents returned an unexpected error.";
    }
}
