import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

import type * as vscode from "vscode";

import type { TailEventsApi } from "./api-client";
import type {
    ApiErrorCategory,
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    BackendTailEvent,
    RelatedEntityViewModel,
    SidebarMessageFromWebview,
    SidebarMessageToWebview,
    SidebarViewModel,
    TimelineItemViewModel,
} from "./types";

const EMPTY_MESSAGE =
    "No entity selected. Run TailEvents: Explain Current Symbol or use View Details from hover.";

interface SidebarProviderOptions {
    apiClient: TailEventsApi;
    templatePath: string;
    getBaseUrl: () => string;
}

export class TailEventsSidebarProvider implements vscode.WebviewViewProvider {
    private readonly apiClient: TailEventsApi;

    private readonly template: string;

    private readonly getBaseUrl: () => string;

    private view: vscode.WebviewView | null = null;

    private currentEntityId: string | null = null;

    private currentAbortController: AbortController | null = null;

    private lastRenderedState: SidebarMessageToWebview = {
        type: "state:empty",
        message: EMPTY_MESSAGE,
    };

    public constructor(options: SidebarProviderOptions) {
        this.apiClient = options.apiClient;
        this.getBaseUrl = options.getBaseUrl;
        this.template = readFileSync(options.templatePath, "utf8");
    }

    public getCurrentEntityId(): string | null {
        return this.currentEntityId;
    }

    public resolveWebviewView(webviewView: vscode.WebviewView): void {
        this.view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
        };
        webviewView.webview.html = this.renderHtml(webviewView.webview.cspSource);
        webviewView.webview.onDidReceiveMessage((message: SidebarMessageFromWebview) => {
            void this.handleMessage(message);
        });
    }

    public async loadEntity(entityId: string): Promise<void> {
        const signal = this.cancelPending();
        this.currentEntityId = entityId;
        this.lastRenderedState = {
            type: "state:loading",
            label: this.currentEntityId === entityId ? this.getCurrentLabel() : undefined,
        };
        await this.postMessage(this.lastRenderedState);

        const [entityResult, explanationResult, eventsResult] = await Promise.all([
            this.apiClient.getEntity(entityId, signal),
            this.apiClient.getExplanationFull(entityId, signal),
            this.apiClient.getEntityEvents(entityId, signal),
        ]);

        if (signal.aborted) {
            return;
        }

        if (!entityResult.ok || !explanationResult.ok) {
            this.lastRenderedState = {
                type: "state:error",
                error: chooseError(entityResult, explanationResult),
                baseUrl: normalizeBaseUrl(this.getBaseUrl()),
            };
            await this.postMessage(this.lastRenderedState);
            return;
        }

        const viewModel = buildViewModel(
            entityResult.data,
            explanationResult.data,
            eventsResult,
        );
        this.lastRenderedState = {
            type: "state:update",
            data: viewModel,
        };
        await this.postMessage(this.lastRenderedState);
    }

    private cancelPending(): AbortSignal {
        if (this.currentAbortController) {
            this.currentAbortController.abort();
        }
        this.currentAbortController = new AbortController();
        return this.currentAbortController.signal;
    }

    private async handleMessage(message: SidebarMessageFromWebview): Promise<void> {
        switch (message.type) {
            case "ready":
                await this.postMessage(this.lastRenderedState);
                return;
            case "refresh":
                if (this.currentEntityId) {
                    await this.loadEntity(this.currentEntityId);
                } else {
                    this.lastRenderedState = {
                        type: "state:empty",
                        message: EMPTY_MESSAGE,
                    };
                    await this.postMessage(this.lastRenderedState);
                }
                return;
            case "openRelatedEntity":
                if (message.entityId) {
                    await this.loadEntity(message.entityId);
                }
                return;
            default:
                return;
        }
    }

    private async postMessage(message: SidebarMessageToWebview): Promise<void> {
        if (!this.view) {
            return;
        }
        await this.view.webview.postMessage(message);
    }

    private renderHtml(cspSource: string): string {
        const nonce = randomBytes(16).toString("base64");
        return this.template
            .replaceAll("__NONCE__", nonce)
            .replaceAll("__CSP_SOURCE__", cspSource);
    }

    private getCurrentLabel(): string | undefined {
        if (this.lastRenderedState.type !== "state:update") {
            return undefined;
        }
        return this.lastRenderedState.data.entityName;
    }
}

function buildViewModel(
    entity: BackendCodeEntity,
    explanation: BackendEntityExplanation,
    eventsResult: ApiResult<BackendTailEvent[]>,
): SidebarViewModel {
    const [lineStart, lineEnd] = entity.line_range ?? [null, null];
    const renameMap = new Map<string, string>();
    for (const renameRecord of entity.rename_history) {
        renameMap.set(
            renameRecord.event_id,
            `Renamed from ${renameRecord.old_qualified_name} to ${renameRecord.new_qualified_name}`,
        );
    }

    const timeline: TimelineItemViewModel[] = eventsResult.ok
        ? [...eventsResult.data]
            .sort((left, right) => {
                return Date.parse(right.timestamp) - Date.parse(left.timestamp);
            })
            .map((event) => {
                return {
                    eventId: event.event_id,
                    timestamp: event.timestamp,
                    actionType: event.action_type,
                    intent: event.intent,
                    reasoning: event.reasoning ?? null,
                    renameLabel: renameMap.get(event.event_id),
                };
            })
        : [];

    const relatedEntities: RelatedEntityViewModel[] = explanation.related_entities.map((item) => {
        const direction = typeof item.direction === "string" ? item.direction : "";
        const relationType = typeof item.relation_type === "string" ? item.relation_type : "";
        return {
            entityId: String(item.entity_id),
            label: item.entity_name || item.qualified_name,
            relationLabel: `${direction} ${relationType}`.trim(),
            qualifiedName: item.qualified_name,
            direction,
        };
    });

    return {
        entityId: entity.entity_id,
        entityName: explanation.entity_name || entity.name,
        entityType: entity.entity_type,
        signature: explanation.signature ?? entity.signature ?? null,
        filePath: entity.file_path,
        lineStart,
        lineEnd,
        eventCount: entity.event_refs.length,
        summary: explanation.summary,
        detailedExplanation: explanation.detailed_explanation ?? null,
        timeline,
        historyAvailable: eventsResult.ok,
        relatedEntities,
    };
}

function chooseError(
    entityResult: ApiResult<BackendCodeEntity>,
    explanationResult: ApiResult<BackendEntityExplanation>,
): ApiErrorCategory {
    if (!entityResult.ok) {
        return entityResult.error;
    }
    if (!explanationResult.ok) {
        return explanationResult.error;
    }
    return "unknown";
}

function normalizeBaseUrl(baseUrl: string): string {
    return baseUrl.trim().replace(/\/+$/, "");
}
