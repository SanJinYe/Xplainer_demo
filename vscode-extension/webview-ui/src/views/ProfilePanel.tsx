import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { useWebviewState } from "../context/WebviewStateContext";

export function ProfilePanel() {
    const { state, actions } = useWebviewState();
    const data = state.profileState.data;

    if (!state.profileState.open) {
        return null;
    }

    return (
        <div className="absolute inset-0 z-20 flex justify-end bg-black/10">
            <button
                type="button"
                className="flex-1 border-0 bg-transparent p-0"
                aria-label="Close profile panel"
                onClick={() => actions.setProfileOpen(false)}
            />
            <Card className="m-3 flex max-h-[calc(100%-1.5rem)] w-[300px] max-w-[calc(100%-1.5rem)] flex-col gap-3 overflow-y-auto bg-[var(--te-bg)] p-3">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <p className="text-sm font-semibold">Profiles</p>
                        <p className="text-xs text-[var(--te-muted)]">
                            Host-owned profile selection stays outside the code transcript.
                        </p>
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => actions.setProfileOpen(false)}>
                        Close
                    </Button>
                </div>
                {data ? (
                    <>
                        <ProfileSummary
                            title="Code Profile"
                            label={data.codeProfile.label}
                            meta={formatProfileMeta(data.codeProfile.backend, data.codeProfile.model, data.codeProfile.source)}
                            reason={data.codeProfile.reason}
                            onSelect={() => actions.send({ type: "selectCodeProfile" })}
                        />
                        <ProfileSummary
                            title="Explain Profile"
                            label={data.explainProfile.label}
                            meta={formatProfileMeta(data.explainProfile.backend, data.explainProfile.model, data.explainProfile.source)}
                            reason={data.explainProfile.reason}
                            onSelect={() => actions.send({ type: "selectExplainProfile" })}
                        />
                        <div className="rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <p className="text-sm font-semibold">Capabilities</p>
                                    <p className="text-xs text-[var(--te-muted)]">
                                        Host availability only.
                                    </p>
                                </div>
                                <Badge variant="accent">{data.capabilitySummary.available.length}</Badge>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {data.capabilitySummary.available.map((item) => {
                                    return (
                                        <Badge key={item.key} variant="subtle" title={item.reason ?? undefined}>
                                            {item.key}
                                        </Badge>
                                    );
                                })}
                                {data.capabilitySummary.unavailableCount > 0 ? (
                                    <Badge variant="warning">{`${data.capabilitySummary.unavailableCount} unavailable`}</Badge>
                                ) : null}
                            </div>
                        </div>
                    </>
                ) : (
                    <p className="text-sm text-[var(--te-muted)]">Waiting for profile state.</p>
                )}
            </Card>
        </div>
    );
}

function ProfileSummary(props: {
    title: string;
    label: string;
    meta: string;
    reason: string | null;
    onSelect: () => void;
}) {
    return (
        <div className="rounded-[16px] border border-[var(--te-border)] bg-[var(--te-bg)] p-3">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <p className="text-sm font-semibold">{props.title}</p>
                    <p className="mt-2 text-base font-medium">{props.label}</p>
                    <p className="mt-1 text-sm text-[var(--te-muted)]">{props.meta}</p>
                    {props.reason ? (
                        <p className="mt-2 text-xs text-[var(--te-muted)]">{props.reason}</p>
                    ) : null}
                </div>
                <Button variant="primary" size="sm" onClick={props.onSelect}>
                    Select
                </Button>
            </div>
        </div>
    );
}

function formatProfileMeta(
    backend: string | null,
    model: string | null,
    source: string | null,
): string {
    const parts = [backend, model, source].filter(Boolean);
    return parts.length > 0 ? parts.join(" / ") : "No backend metadata.";
}
