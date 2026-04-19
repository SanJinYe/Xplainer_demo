import type { HistoryDetailStepViewModel } from "../../../src/types";
import { Badge } from "./badge";
import { Card } from "./card";

export function StepList(props: { steps: HistoryDetailStepViewModel[] }) {
    if (props.steps.length === 0) {
        return <p className="text-sm text-[var(--te-muted)]">No recorded steps.</p>;
    }

    return (
        <div className="space-y-3">
            {props.steps.map((step) => {
                return (
                    <Card key={step.stepId} className="border-dashed px-4 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge>{step.stepKind}</Badge>
                            <Badge variant="subtle">{step.status}</Badge>
                            {step.toolName ? <Badge variant="accent">{step.toolName}</Badge> : null}
                            <span className="text-xs text-[var(--te-muted)]">{step.timestamp}</span>
                        </div>
                        <p className="mt-3 text-sm font-medium">{step.filePath}</p>
                        <p className="mt-1 text-sm text-[var(--te-muted)]">{step.summary}</p>
                    </Card>
                );
            })}
        </div>
    );
}
