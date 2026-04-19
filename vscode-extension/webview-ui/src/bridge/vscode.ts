import type { SidebarMessageFromWebview } from "../../../src/types";

export interface VsCodeBridge<TState> {
    postMessage: (message: SidebarMessageFromWebview) => void;
    getState: () => TState | undefined;
    setState: (state: TState) => void;
}

interface VsCodeApi<TState> {
    postMessage(message: SidebarMessageFromWebview): void;
    getState(): TState | undefined;
    setState(state: TState): TState;
}

declare global {
    function acquireVsCodeApi<TState>(): VsCodeApi<TState>;
}

let cachedApi: VsCodeApi<unknown> | null = null;

export function createVsCodeBridge<TState>(): VsCodeBridge<TState> {
    if (cachedApi === null) {
        cachedApi = acquireVsCodeApi<TState>() as VsCodeApi<unknown>;
    }
    const api = cachedApi as VsCodeApi<TState>;
    return {
        postMessage(message) {
            api.postMessage(message);
        },
        getState() {
            return api.getState();
        },
        setState(state) {
            api.setState(state);
        },
    };
}
