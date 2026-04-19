import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card } from "./components/ui/card";
import { useWebviewState } from "./context/WebviewStateContext";
import { CodeWorkbenchView } from "./views/CodeWorkbenchView";
import { ExplainView } from "./views/ExplainView";
import { HistoryView } from "./views/HistoryView";
import { ProfilesView } from "./views/ProfilesView";

const TABS = [
    { id: "explain", label: "Explain" },
    { id: "code", label: "Code" },
    { id: "history", label: "History" },
    { id: "profiles", label: "Profiles" },
] as const;

export default function App() {
    const { state, actions } = useWebviewState();
    const activeView = state.ui.activeView;
    const codeState = state.codeState;

    return (
        <div className="flex h-full flex-col bg-[var(--te-bg)] text-[var(--te-foreground)]">
            <header className="border-b border-[var(--te-border)] px-4 py-4">
                <div className="flex items-center justify-between gap-3">
                    <div className="space-y-1">
                        <p className="text-[11px] uppercase tracking-[0.16em] text-[var(--te-muted)]">
                            TailEvents
                        </p>
                        <h1 className="text-[18px] font-semibold">Workbench</h1>
                    </div>
                    <div className="flex items-center gap-2">
                        <Badge>{state.hostMode}</Badge>
                        {codeState?.status ? <Badge variant="subtle">{codeState.status}</Badge> : null}
                    </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                    {TABS.map((tab) => {
                        const active = activeView === tab.id;
                        return (
                            <Button
                                key={tab.id}
                                variant={active ? "primary" : "ghost"}
                                onClick={() => actions.setActiveView(tab.id)}
                            >
                                {tab.label}
                            </Button>
                        );
                    })}
                </div>
            </header>
            <main className="min-h-0 flex-1 overflow-hidden p-4">
                <Card className="h-full overflow-hidden">
                    {activeView === "explain" ? <ExplainView /> : null}
                    {activeView === "code" ? <CodeWorkbenchView /> : null}
                    {activeView === "history" ? <HistoryView /> : null}
                    {activeView === "profiles" ? <ProfilesView /> : null}
                </Card>
            </main>
        </div>
    );
}
