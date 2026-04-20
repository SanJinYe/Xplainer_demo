import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { useWebviewState } from "./context/WebviewStateContext";
import { CodeWorkbenchView } from "./views/CodeWorkbenchView";
import { ExplainView } from "./views/ExplainView";
import { HistoryView } from "./views/HistoryView";
import { ProfilePanel } from "./views/ProfilePanel";

const TABS = [
    { id: "code", label: "Code" },
    { id: "explain", label: "Explain" },
    { id: "history", label: "History" },
] as const;

export default function App() {
    const { state, actions } = useWebviewState();
    const activeView = state.persisted.activeView;
    const contentClassName =
        activeView === "code"
            ? "flex min-h-0 flex-1 flex-col overflow-hidden"
            : "min-h-0 flex-1 overflow-y-auto";

    return (
        <div className="relative flex h-full flex-col bg-[var(--te-bg)] text-[var(--te-foreground)]">
            <header className="border-b border-[var(--te-border)] bg-[var(--te-bg)] px-3 py-2">
                <div className="flex items-center justify-between gap-3">
                    <nav className="flex items-center gap-1 rounded-full border border-[var(--te-border)] bg-[var(--te-surface)] p-1">
                        {TABS.map((tab) => {
                            const active = activeView === tab.id;
                            return (
                                <Button
                                    key={tab.id}
                                    variant={active ? "primary" : "ghost"}
                                    size="sm"
                                    className={active ? "shadow-none" : "border-transparent bg-transparent"}
                                    onClick={() => actions.setActiveView(tab.id)}
                                >
                                    {tab.label}
                                </Button>
                            );
                        })}
                    </nav>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="subtle"
                            size="sm"
                            className="shadow-none"
                            onClick={() => actions.send({ type: "onboardRepository" })}
                        >
                            Onboard
                        </Button>
                        <Button
                            variant={state.profileState.open ? "subtle" : "ghost"}
                            size="sm"
                            className="border-transparent"
                            onClick={() => actions.setProfileOpen(!state.profileState.open)}
                        >
                            Profile
                        </Button>
                    </div>
                </div>
            </header>
            <main className={contentClassName}>
                {activeView === "explain" ? <ExplainView /> : null}
                {activeView === "code" ? <CodeWorkbenchView /> : null}
                {activeView === "history" ? <HistoryView /> : null}
            </main>
            <ProfilePanel />
        </div>
    );
}
