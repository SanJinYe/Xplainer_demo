import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { useWebviewState } from "../context/WebviewStateContext";

export function ProfilesView() {
    const { state, actions } = useWebviewState();
    const data = state.codeState;

    if (!data) {
        return (
            <div className="flex h-full items-center justify-center p-6">
                <p className="text-sm text-[var(--te-muted)]">Waiting for profile state.</p>
            </div>
        );
    }

    return (
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
            <div className="grid gap-4 xl:grid-cols-2">
                <ProfileCard
                    title="Code Profile"
                    label={data.codeProfile.label}
                    meta={formatProfileMeta(data.codeProfile.backend, data.codeProfile.model, data.codeProfile.source)}
                    reason={data.codeProfile.reason}
                    onSelect={() => actions.send({ type: "selectCodeProfile" })}
                />
                <ProfileCard
                    title="Explain Profile"
                    label={data.explainProfile.label}
                    meta={formatProfileMeta(data.explainProfile.backend, data.explainProfile.model, data.explainProfile.source)}
                    reason={data.explainProfile.reason}
                    onSelect={() => actions.send({ type: "selectExplainProfile" })}
                />
            </div>
            <Card className="p-4">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Capabilities</h2>
                        <p className="text-sm text-[var(--te-muted)]">
                            TailEvents keeps capability gating in the host; the Webview only renders it.
                        </p>
                    </div>
                    <Badge variant="accent">{data.capabilitySummary.available.length} available</Badge>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
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
            </Card>
        </div>
    );
}

function ProfileCard(props: {
    title: string;
    label: string;
    meta: string;
    reason: string | null;
    onSelect: () => void;
}) {
    return (
        <Card className="p-4">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-sm font-semibold">{props.title}</p>
                    <p className="mt-2 text-lg font-medium">{props.label}</p>
                    <p className="mt-1 text-sm text-[var(--te-muted)]">{props.meta}</p>
                    {props.reason ? (
                        <p className="mt-2 text-sm text-[var(--te-muted)]">{props.reason}</p>
                    ) : null}
                </div>
                <Button variant="primary" onClick={props.onSelect}>
                    Select
                </Button>
            </div>
        </Card>
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
