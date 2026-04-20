import type {
    CodeConversationRunViewModel,
    CodeViewModel,
    SidebarEmptyMessage,
    SidebarErrorMessage,
    SidebarLoadingMessage,
    SidebarMessageToWebview,
    SidebarMode,
    SidebarUpdateMessage,
} from "../../../src/types";

export type ActiveView = "explain" | "code" | "history";

export interface WebviewPersistedState {
    activeView: ActiveView;
    promptDraft: string;
    collapsedPanels: Record<string, boolean>;
}

export type ExplainHostState =
    | SidebarEmptyMessage
    | SidebarLoadingMessage
    | SidebarUpdateMessage
    | SidebarErrorMessage;

export interface HistoryViewState {
    data: CodeViewModel | null;
}

export interface ProfilePanelState {
    open: boolean;
    data: CodeViewModel | null;
}

export interface WebviewUiState {
    dismissedResultMessageId: string | null;
}

export interface WebviewAppState {
    hostMode: SidebarMode;
    explainState: ExplainHostState;
    codeState: CodeViewModel | null;
    historyState: HistoryViewState;
    profileState: ProfilePanelState;
    persisted: WebviewPersistedState;
    ui: WebviewUiState;
}

const EXPLAIN_PANEL_IDS = [
    "explain.summary",
    "explain.relations",
    "explain.docs",
] as const;

type Action =
    | { type: "host/message"; message: SidebarMessageToWebview }
    | { type: "ui/setActiveView"; activeView: ActiveView }
    | { type: "ui/setPromptDraft"; promptDraft: string }
    | { type: "ui/setPanelCollapsed"; panelId: string; collapsed: boolean }
    | { type: "ui/setProfileOpen"; open: boolean }
    | { type: "ui/dismissDraftReady" }
    | { type: "ui/showDraftReady" };

const EMPTY_EXPLAIN_STATE: SidebarEmptyMessage = {
    type: "state:empty",
    message: "No entity selected.",
};

export function createInitialState(
    persisted?: Partial<WebviewPersistedState>,
): WebviewAppState {
    return {
        hostMode: "explain",
        explainState: EMPTY_EXPLAIN_STATE,
        codeState: null,
        historyState: {
            data: null,
        },
        profileState: {
            open: false,
            data: null,
        },
        persisted: {
            activeView: persisted?.activeView ?? "explain",
            promptDraft: persisted?.promptDraft ?? "",
            collapsedPanels: persisted?.collapsedPanels ?? {},
        },
        ui: {
            dismissedResultMessageId: null,
        },
    };
}

export function reducer(state: WebviewAppState, action: Action): WebviewAppState {
    switch (action.type) {
        case "host/message":
            return applyHostMessage(state, action.message);
        case "ui/setActiveView":
            return {
                ...state,
                persisted: {
                    ...state.persisted,
                    activeView: action.activeView,
                },
            };
        case "ui/setPromptDraft":
            return {
                ...state,
                persisted: {
                    ...state.persisted,
                    promptDraft: action.promptDraft,
                },
            };
        case "ui/setPanelCollapsed":
            return {
                ...state,
                persisted: {
                    ...state.persisted,
                    collapsedPanels: {
                        ...state.persisted.collapsedPanels,
                        [action.panelId]: action.collapsed,
                    },
                },
            };
        case "ui/setProfileOpen":
            return {
                ...state,
                profileState: {
                    ...state.profileState,
                    open: action.open,
                },
            };
        case "ui/dismissDraftReady":
            return {
                ...state,
                ui: {
                    ...state.ui,
                    dismissedResultMessageId: findLatestReadyResultMessageId(state.codeState?.conversation.runs ?? []),
                },
            };
        case "ui/showDraftReady":
            return {
                ...state,
                ui: {
                    ...state.ui,
                    dismissedResultMessageId: null,
                },
            };
        default:
            return state;
    }
}

export function createActions(dispatch: (action: Action) => void) {
    return {
        applyHostMessage(message: SidebarMessageToWebview) {
            dispatch({ type: "host/message", message });
        },
        setActiveView(activeView: ActiveView) {
            dispatch({ type: "ui/setActiveView", activeView });
        },
        setPromptDraft(promptDraft: string) {
            dispatch({ type: "ui/setPromptDraft", promptDraft });
        },
        setPanelCollapsed(panelId: string, collapsed: boolean) {
            dispatch({ type: "ui/setPanelCollapsed", panelId, collapsed });
        },
        setProfileOpen(open: boolean) {
            dispatch({ type: "ui/setProfileOpen", open });
        },
        dismissDraftReady() {
            dispatch({ type: "ui/dismissDraftReady" });
        },
        showDraftReady() {
            dispatch({ type: "ui/showDraftReady" });
        },
    };
}

export function getPersistedState(state: WebviewAppState): WebviewPersistedState {
    return state.persisted;
}

function applyHostMessage(
    state: WebviewAppState,
    message: SidebarMessageToWebview,
): WebviewAppState {
    switch (message.type) {
        case "mode:update":
            return {
                ...state,
                hostMode: message.mode,
            };
        case "code:update": {
            const latestReadyResultMessageId = findLatestReadyResultMessageId(message.data.conversation.runs);
            return {
                ...state,
                codeState: message.data,
                historyState: {
                    data: message.data,
                },
                profileState: {
                    ...state.profileState,
                    data: message.data,
                },
                ui: {
                    ...state.ui,
                    dismissedResultMessageId:
                        message.data.status === "ready_to_apply" &&
                        state.ui.dismissedResultMessageId === latestReadyResultMessageId
                            ? state.ui.dismissedResultMessageId
                            : null,
                },
            };
        }
        case "code:fillPrompt":
            return {
                ...state,
                persisted: {
                    ...state.persisted,
                    promptDraft: message.prompt,
                },
            };
        case "state:empty":
            return {
                ...state,
                explainState: message,
            };
        case "state:loading":
            return {
                ...state,
                explainState: message,
                persisted: {
                    ...state.persisted,
                    activeView: "explain",
                    collapsedPanels: expandPanels(
                        state.persisted.collapsedPanels,
                        EXPLAIN_PANEL_IDS,
                    ),
                },
            };
        case "state:update":
        case "state:error":
            return {
                ...state,
                explainState: message,
            };
        default:
            return state;
    }
}

function expandPanels(
    collapsedPanels: Record<string, boolean>,
    panelIds: readonly string[],
): Record<string, boolean> {
    const next = { ...collapsedPanels };
    for (const panelId of panelIds) {
        delete next[panelId];
    }
    return next;
}

function findLatestReadyResultMessageId(runs: CodeConversationRunViewModel[]): string | null {
    for (let runIndex = runs.length - 1; runIndex >= 0; runIndex -= 1) {
        const run = runs[runIndex];
        if (run.status !== "ready_to_apply") {
            continue;
        }
        for (let messageIndex = run.messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
            const message = run.messages[messageIndex];
            if (message.kind === "assistant_result") {
                return message.id;
            }
        }
    }
    return null;
}
