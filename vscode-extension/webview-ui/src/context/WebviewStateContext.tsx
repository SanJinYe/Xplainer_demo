import {
    createContext,
    useContext,
    useEffect,
    useMemo,
    useReducer,
} from "react";
import type { ReactNode } from "react";

import type { SidebarMessageFromWebview, SidebarMessageToWebview } from "../../../src/types";
import { createVsCodeBridge } from "../bridge/vscode";
import {
    createActions,
    createInitialState,
    getPersistedState,
    reducer,
    type ActiveView,
    type WebviewAppState,
} from "../state/reducer";

interface WebviewActions {
    setActiveView: (activeView: ActiveView) => void;
    setPromptDraft: (promptDraft: string) => void;
    setPanelCollapsed: (panelId: string, collapsed: boolean) => void;
    setProfileOpen: (open: boolean) => void;
    dismissDraftReady: () => void;
    showDraftReady: () => void;
    send: (message: SidebarMessageFromWebview) => void;
}

interface WebviewContextValue {
    state: WebviewAppState;
    actions: WebviewActions;
}

const WebviewStateContext = createContext<WebviewContextValue | null>(null);

export function WebviewStateProvider(props: { children: ReactNode }) {
    const bridge = useMemo(() => createVsCodeBridge(), []);
    const [state, dispatch] = useReducer(
        reducer,
        bridge.getState(),
        createInitialState,
    );

    const reducerActions = useMemo(() => createActions(dispatch), []);

    useEffect(() => {
        const handleMessage = (event: MessageEvent<SidebarMessageToWebview>) => {
            reducerActions.applyHostMessage(event.data);
        };
        window.addEventListener("message", handleMessage);
        bridge.postMessage({ type: "ready" });
        return () => {
            window.removeEventListener("message", handleMessage);
        };
    }, [bridge, reducerActions]);

    useEffect(() => {
        bridge.setState(getPersistedState(state));
    }, [bridge, state]);

    useEffect(() => {
        const desiredMode = state.persisted.activeView === "explain" ? "explain" : "code";
        if (state.hostMode !== desiredMode) {
            bridge.postMessage({
                type: "setMode",
                mode: desiredMode,
            });
        }
    }, [bridge, state.hostMode, state.persisted.activeView]);

    const actions = useMemo<WebviewActions>(() => {
        return {
            setActiveView(activeView) {
                reducerActions.setActiveView(activeView);
            },
            setPromptDraft(promptDraft) {
                reducerActions.setPromptDraft(promptDraft);
            },
            setPanelCollapsed(panelId, collapsed) {
                reducerActions.setPanelCollapsed(panelId, collapsed);
            },
            setProfileOpen(open) {
                reducerActions.setProfileOpen(open);
            },
            dismissDraftReady() {
                reducerActions.dismissDraftReady();
            },
            showDraftReady() {
                reducerActions.showDraftReady();
            },
            send(message) {
                bridge.postMessage(message);
            },
        };
    }, [bridge, reducerActions]);

    const value = useMemo(() => {
        return {
            state,
            actions,
        };
    }, [actions, state]);

    return (
        <WebviewStateContext.Provider value={value}>
            {props.children}
        </WebviewStateContext.Provider>
    );
}

export function useWebviewState(): WebviewContextValue {
    const value = useContext(WebviewStateContext);
    if (!value) {
        throw new Error("WebviewStateProvider is missing.");
    }
    return value;
}
