import type {
    CodeViewModel,
    SidebarEmptyMessage,
    SidebarErrorMessage,
    SidebarLoadingMessage,
    SidebarMessageToWebview,
    SidebarMode,
    SidebarUpdateMessage,
} from "../../../src/types";

export type ActiveView = "explain" | "code" | "history" | "profiles";

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

export interface WebviewAppState {
    hostMode: SidebarMode;
    explainState: ExplainHostState;
    codeState: CodeViewModel | null;
    ui: WebviewPersistedState;
}

type Action =
    | { type: "host/message"; message: SidebarMessageToWebview }
    | { type: "ui/setActiveView"; activeView: ActiveView }
    | { type: "ui/setPromptDraft"; promptDraft: string }
    | { type: "ui/setPanelCollapsed"; panelId: string; collapsed: boolean };

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
        ui: {
            activeView: persisted?.activeView ?? "explain",
            promptDraft: persisted?.promptDraft ?? "",
            collapsedPanels: persisted?.collapsedPanels ?? {},
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
                ui: {
                    ...state.ui,
                    activeView: action.activeView,
                },
            };
        case "ui/setPromptDraft":
            return {
                ...state,
                ui: {
                    ...state.ui,
                    promptDraft: action.promptDraft,
                },
            };
        case "ui/setPanelCollapsed":
            return {
                ...state,
                ui: {
                    ...state.ui,
                    collapsedPanels: {
                        ...state.ui.collapsedPanels,
                        [action.panelId]: action.collapsed,
                    },
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
    };
}

export function getPersistedState(state: WebviewAppState): WebviewPersistedState {
    return state.ui;
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
        case "code:update":
            return {
                ...state,
                codeState: message.data,
            };
        case "code:fillPrompt":
            return {
                ...state,
                ui: {
                    ...state.ui,
                    promptDraft: message.prompt,
                },
            };
        case "state:empty":
        case "state:loading":
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
