import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";

import type * as vscode from "vscode";

import type { TailEventsApi } from "./api-client";
import { findEntityByLocation } from "./location-lookup";
import { getFileLookupCandidates, toWorkspaceRelativePath } from "./path-utils";
import type {
    ApiErrorCategory,
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    BackendTailEvent,
    CodeTaskStatus,
    CodeViewModel,
    CodingTaskResult,
    CreateRawEventPayload,
    RelatedEntityViewModel,
    SidebarMessageFromWebview,
    SidebarMessageToWebview,
    SidebarMode,
    SidebarViewModel,
    TimelineItemViewModel,
} from "./types";

const EMPTY_MESSAGE =
    "No entity selected. Run TailEvents: Explain Current Symbol or use View Details from hover.";
const READY_TO_RUN_MESSAGE = "Ready to run a single-turn task on the current Python file.";
const NO_ACTIVE_EDITOR_MESSAGE = "No active editor.";
const UNSAVED_FILE_MESSAGE = "Only saved Python files are supported.";
const NON_FILE_MESSAGE = "Only local files are supported.";
const NON_PYTHON_FILE_MESSAGE = "Only Python files are supported.";
const OUTSIDE_WORKSPACE_MESSAGE = "The active file must be inside the current workspace.";
const EMPTY_PROMPT_MESSAGE = "Prompt is required.";
const TASK_RUNNING_MESSAGE = "Generating update...";
const TASK_READY_MESSAGE = "Generation finished. Apply is available.";
const TASK_CANCELLED_MESSAGE = "Task cancelled.";
const APPLYING_MESSAGE = "Applying generated content and writing a TailEvent...";
const APPLY_SUCCESS_MESSAGE = "File updated and event written.";
const APPLY_SUCCESS_NO_ENTITY_MESSAGE = "File updated and event written. Re-run explain if needed.";
const EVENT_RETRY_MESSAGE = "Retrying TailEvent write...";
const EVENT_RETRY_FAILED_MESSAGE = "File updated, but TailEvent write failed. Retry is available.";
const FILE_CHANGED_MESSAGE = "The file changed after generation. Please run again.";
const APPLY_FAILED_MESSAGE = "Failed to apply the generated content. Please run again.";

interface SidebarRuntime {
    getActiveEditor: () => vscode.TextEditor | null;
    getWorkspaceFolders: () => readonly vscode.WorkspaceFolder[] | undefined;
    replaceDocumentContent: (
        editor: vscode.TextEditor,
        content: string,
    ) => Promise<boolean>;
    saveDocument: (document: vscode.TextDocument) => Promise<boolean>;
}

interface SidebarProviderOptions {
    apiClient: TailEventsApi;
    templatePath: string;
    getBaseUrl: () => string;
    runtime: SidebarRuntime;
    generateSessionId?: () => string;
}

interface EditorContext {
    editor: vscode.TextEditor;
    absolutePath: string;
    workspaceFilePath: string;
    version: number;
    content: string;
}

interface CodeAvailability {
    context: EditorContext | null;
    filePath: string | null;
    reason: string | null;
}

interface TaskContext {
    absolutePath: string;
    workspaceFilePath: string;
    documentVersion: number;
    lineNumber: number;
    fileContent: string;
    userPrompt: string;
}

export class TailEventsSidebarProvider implements vscode.WebviewViewProvider {
    private readonly apiClient: TailEventsApi;

    private readonly template: string;

    private readonly getBaseUrl: () => string;

    private readonly runtime: SidebarRuntime;

    private readonly generateSessionId: () => string;

    private view: vscode.WebviewView | null = null;

    private currentEntityId: string | null = null;

    private currentMode: SidebarMode = "explain";

    private currentAbortController: AbortController | null = null;

    private currentTaskAbortController: AbortController | null = null;

    private currentTaskContext: TaskContext | null = null;

    private currentTaskResult: CodingTaskResult | null = null;

    private pendingEventPayload: CreateRawEventPayload | null = null;

    private codeStatus: CodeTaskStatus = "idle";

    private codeStreamedText = "";

    private codeMessage: string | null = null;

    private lastExplainState: SidebarMessageToWebview = {
        type: "state:empty",
        message: EMPTY_MESSAGE,
    };

    public constructor(options: SidebarProviderOptions) {
        this.apiClient = options.apiClient;
        this.getBaseUrl = options.getBaseUrl;
        this.runtime = options.runtime;
        this.generateSessionId = options.generateSessionId ?? defaultSessionId;
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
        const signal = this.cancelPendingExplain();
        this.currentEntityId = entityId;
        this.lastExplainState = {
            type: "state:loading",
            label: this.currentEntityId === entityId ? this.getCurrentLabel() : undefined,
        };
        await this.postMessage(this.lastExplainState);

        const [entityResult, explanationResult, eventsResult] = await Promise.all([
            this.apiClient.getEntity(entityId, signal),
            this.apiClient.getExplanationFull(entityId, signal),
            this.apiClient.getEntityEvents(entityId, signal),
        ]);

        if (signal.aborted) {
            return;
        }

        if (!entityResult.ok || !explanationResult.ok) {
            this.lastExplainState = {
                type: "state:error",
                error: chooseError(entityResult, explanationResult),
                baseUrl: normalizeBaseUrl(this.getBaseUrl()),
            };
            await this.postMessage(this.lastExplainState);
            return;
        }

        const viewModel = buildViewModel(
            entityResult.data,
            explanationResult.data,
            eventsResult,
        );
        this.lastExplainState = {
            type: "state:update",
            data: viewModel,
        };
        await this.postMessage(this.lastExplainState);
    }

    public async refreshCodeContext(): Promise<void> {
        await this.postCodeState();
    }

    private cancelPendingExplain(): AbortSignal {
        if (this.currentAbortController) {
            this.currentAbortController.abort();
        }
        this.currentAbortController = new AbortController();
        return this.currentAbortController.signal;
    }

    private async handleMessage(message: SidebarMessageFromWebview): Promise<void> {
        switch (message.type) {
            case "ready":
                await this.postMessage({
                    type: "mode:update",
                    mode: this.currentMode,
                });
                await this.postCodeState();
                await this.postMessage(this.lastExplainState);
                return;
            case "refresh":
                if (this.currentEntityId) {
                    await this.loadEntity(this.currentEntityId);
                } else {
                    this.lastExplainState = {
                        type: "state:empty",
                        message: EMPTY_MESSAGE,
                    };
                    await this.postMessage(this.lastExplainState);
                }
                return;
            case "setMode":
                this.currentMode = message.mode;
                await this.postMessage({
                    type: "mode:update",
                    mode: this.currentMode,
                });
                await this.postCodeState();
                return;
            case "openRelatedEntity":
                if (message.entityId) {
                    await this.loadEntity(message.entityId);
                }
                return;
            case "runTask":
                await this.runTask(message.prompt);
                return;
            case "cancelTask":
                await this.cancelTask();
                return;
            case "applyTask":
                await this.applyTask();
                return;
            case "retryEventWrite":
                await this.retryEventWrite();
                return;
            default:
                return;
        }
    }

    private async runTask(prompt: string): Promise<void> {
        const trimmedPrompt = prompt.trim();
        if (!trimmedPrompt) {
            await this.setCodeState("error", EMPTY_PROMPT_MESSAGE, "");
            return;
        }

        const availability = this.getCodeAvailability();
        if (!availability.context) {
            await this.setCodeState(
                "error",
                availability.reason ?? NO_ACTIVE_EDITOR_MESSAGE,
                "",
            );
            return;
        }

        this.currentTaskResult = null;
        this.pendingEventPayload = null;
        this.currentTaskContext = {
            absolutePath: availability.context.absolutePath,
            workspaceFilePath: availability.context.workspaceFilePath,
            documentVersion: availability.context.version,
            lineNumber: availability.context.editor.selection.active.line + 1,
            fileContent: availability.context.content,
            userPrompt: trimmedPrompt,
        };
        this.currentTaskAbortController?.abort();
        const controller = new AbortController();
        this.currentTaskAbortController = controller;

        await this.setCodeState("running", TASK_RUNNING_MESSAGE, "");

        const result = await this.apiClient.runCodingTaskStream(
            {
                file_path: this.currentTaskContext.workspaceFilePath,
                file_content: this.currentTaskContext.fileContent,
                user_prompt: this.currentTaskContext.userPrompt,
            },
            {
                onDelta: (text) => {
                    if (controller.signal.aborted) {
                        return;
                    }
                    this.codeStreamedText += text;
                    void this.postCodeState();
                },
            },
            controller.signal,
        );

        if (this.currentTaskAbortController !== controller || controller.signal.aborted) {
            return;
        }
        this.currentTaskAbortController = null;

        if (!result.ok) {
            this.currentTaskContext = null;
            await this.setCodeState("error", formatTaskError(result.error), this.codeStreamedText);
            return;
        }

        const validationError = validateTaskResult(
            result.data,
            this.currentTaskContext.fileContent,
        );
        if (validationError) {
            this.currentTaskContext = null;
            await this.setCodeState("error", validationError, this.codeStreamedText);
            return;
        }

        this.currentTaskResult = result.data;
        this.pendingEventPayload = buildRawEventPayload(
            this.currentTaskContext.workspaceFilePath,
            result.data,
            this.generateSessionId(),
        );
        await this.setCodeState("ready_to_apply", TASK_READY_MESSAGE, this.codeStreamedText);
    }

    private async cancelTask(): Promise<void> {
        if (!this.currentTaskAbortController) {
            return;
        }

        this.currentTaskAbortController.abort();
        this.currentTaskAbortController = null;
        this.currentTaskContext = null;
        this.currentTaskResult = null;
        this.pendingEventPayload = null;
        await this.setCodeState("idle", TASK_CANCELLED_MESSAGE, "");
    }

    private async applyTask(): Promise<void> {
        if (!this.currentTaskContext || !this.currentTaskResult || !this.pendingEventPayload) {
            await this.setCodeState("error", APPLY_FAILED_MESSAGE, this.codeStreamedText);
            return;
        }

        const editor = this.runtime.getActiveEditor();
        if (
            !editor ||
            editor.document.uri.scheme !== "file" ||
            editor.document.uri.fsPath !== this.currentTaskContext.absolutePath ||
            editor.document.version !== this.currentTaskContext.documentVersion
        ) {
            this.clearPendingTask();
            await this.setCodeState("error", FILE_CHANGED_MESSAGE, this.codeStreamedText);
            return;
        }

        await this.setCodeState("applying", APPLYING_MESSAGE, this.codeStreamedText);

        const replaced = await this.runtime.replaceDocumentContent(
            editor,
            this.currentTaskResult.updated_file_content,
        );
        if (!replaced) {
            this.clearPendingTask();
            await this.setCodeState("error", APPLY_FAILED_MESSAGE, this.codeStreamedText);
            return;
        }

        const saved = await this.runtime.saveDocument(editor.document);
        if (!saved) {
            this.clearPendingTask();
            await this.setCodeState("error", APPLY_FAILED_MESSAGE, this.codeStreamedText);
            return;
        }

        const eventResult = await this.apiClient.createEvent(this.pendingEventPayload);
        if (!eventResult.ok) {
            await this.setCodeState("error", EVENT_RETRY_FAILED_MESSAGE, this.codeStreamedText);
            return;
        }

        await this.handleEventWriteSuccess(editor);
    }

    private async retryEventWrite(): Promise<void> {
        if (!this.pendingEventPayload) {
            return;
        }

        await this.setCodeState("applying", EVENT_RETRY_MESSAGE, this.codeStreamedText);
        const result = await this.apiClient.createEvent(this.pendingEventPayload);
        if (!result.ok) {
            await this.setCodeState("error", EVENT_RETRY_FAILED_MESSAGE, this.codeStreamedText);
            return;
        }

        const editor = this.runtime.getActiveEditor();
        await this.handleEventWriteSuccess(editor);
    }

    private async handleEventWriteSuccess(
        editor: vscode.TextEditor | null,
    ): Promise<void> {
        this.clearPendingTask();
        await this.setCodeState("applied", APPLY_SUCCESS_MESSAGE, this.codeStreamedText);

        const refreshed = await this.refreshExplainForEditor(editor);
        if (!refreshed) {
            await this.setCodeState(
                "applied",
                APPLY_SUCCESS_NO_ENTITY_MESSAGE,
                this.codeStreamedText,
            );
        }
    }

    private async refreshExplainForEditor(
        editor: vscode.TextEditor | null,
    ): Promise<boolean> {
        if (!editor || editor.document.isUntitled || editor.document.uri.scheme !== "file") {
            return false;
        }

        const fileCandidates = getFileLookupCandidates(
            editor.document.uri.fsPath,
            this.runtime.getWorkspaceFolders(),
        );
        if (fileCandidates.length === 0) {
            return false;
        }

        const lookup = await findEntityByLocation(
            this.apiClient,
            fileCandidates,
            editor.selection.active.line + 1,
        );
        if (!lookup.result.ok) {
            return false;
        }

        await this.loadEntity(lookup.result.data.entity_id);
        return true;
    }

    private clearPendingTask(): void {
        this.currentTaskContext = null;
        this.currentTaskResult = null;
        this.pendingEventPayload = null;
    }

    private getCodeAvailability(): CodeAvailability {
        const editor = this.runtime.getActiveEditor();
        if (!editor) {
            return {
                context: null,
                filePath: null,
                reason: NO_ACTIVE_EDITOR_MESSAGE,
            };
        }

        const document = editor.document;
        if (document.isUntitled) {
            return {
                context: null,
                filePath: null,
                reason: UNSAVED_FILE_MESSAGE,
            };
        }
        if (document.uri.scheme !== "file") {
            return {
                context: null,
                filePath: null,
                reason: NON_FILE_MESSAGE,
            };
        }
        if (document.languageId !== "python") {
            return {
                context: null,
                filePath: document.uri.fsPath,
                reason: NON_PYTHON_FILE_MESSAGE,
            };
        }

        const workspaceFilePath = toWorkspaceRelativePath(
            document.uri.fsPath,
            this.runtime.getWorkspaceFolders(),
        );
        if (!workspaceFilePath) {
            return {
                context: null,
                filePath: document.uri.fsPath,
                reason: OUTSIDE_WORKSPACE_MESSAGE,
            };
        }

        return {
            context: {
                editor,
                absolutePath: document.uri.fsPath,
                workspaceFilePath,
                version: document.version,
                content: document.getText(),
            },
            filePath: workspaceFilePath,
            reason: null,
        };
    }

    private buildCodeViewModel(): CodeViewModel {
        const availability = this.getCodeAvailability();
        const hasPendingResult = Boolean(this.currentTaskResult || this.pendingEventPayload);
        const filePath = this.currentTaskContext?.workspaceFilePath ?? availability.filePath;
        const canRun =
            Boolean(availability.context) &&
            !hasPendingResult &&
            this.codeStatus !== "running" &&
            this.codeStatus !== "applying";

        return {
            filePath,
            status: this.codeStatus,
            streamedText: this.codeStreamedText,
            message: this.codeMessage ?? defaultCodeMessage(this.codeStatus, availability),
            canRun,
            canCancel: this.codeStatus === "running",
            canApply: this.codeStatus === "ready_to_apply" && this.currentTaskResult !== null,
            canRetryEventWrite:
                this.codeStatus === "error" && this.pendingEventPayload !== null,
        };
    }

    private async setCodeState(
        status: CodeTaskStatus,
        message: string | null,
        streamedText: string,
    ): Promise<void> {
        this.codeStatus = status;
        this.codeMessage = message;
        this.codeStreamedText = streamedText;
        await this.postCodeState();
    }

    private async postCodeState(): Promise<void> {
        await this.postMessage({
            type: "code:update",
            data: this.buildCodeViewModel(),
        });
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
        if (this.lastExplainState.type !== "state:update") {
            return undefined;
        }
        return this.lastExplainState.data.entityName;
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

function defaultSessionId(): string {
    return `b0_${randomBytes(6).toString("hex")}`;
}

function validateTaskResult(
    result: CodingTaskResult,
    originalContent: string,
): string | null {
    if (!result.updated_file_content.trim()) {
        return "Model returned empty file content.";
    }
    if (result.updated_file_content === originalContent) {
        return "Model did not change the file content.";
    }
    if (!result.intent.trim()) {
        return "Model returned an empty intent.";
    }
    if (result.action_type !== "create" && result.action_type !== "modify") {
        return "Model returned an invalid action type.";
    }
    return null;
}

function buildRawEventPayload(
    filePath: string,
    result: CodingTaskResult,
    sessionId: string,
): CreateRawEventPayload {
    return {
        action_type: result.action_type,
        file_path: filePath,
        code_snapshot: result.updated_file_content,
        intent: result.intent,
        reasoning: result.reasoning ?? null,
        decision_alternatives: null,
        session_id: sessionId,
        line_range: null,
        external_refs: [],
    };
}

function defaultCodeMessage(
    status: CodeTaskStatus,
    availability: CodeAvailability,
): string {
    if (status === "idle") {
        return availability.reason ?? READY_TO_RUN_MESSAGE;
    }
    if (status === "applied") {
        return APPLY_SUCCESS_MESSAGE;
    }
    if (status === "error") {
        return availability.reason ?? "Task failed.";
    }
    if (status === "running") {
        return TASK_RUNNING_MESSAGE;
    }
    if (status === "ready_to_apply") {
        return TASK_READY_MESSAGE;
    }
    return APPLYING_MESSAGE;
}

function formatTaskError(error: ApiErrorCategory): string {
    switch (error) {
        case "backend_unavailable":
            return "TailEvents backend is unavailable.";
        case "timeout":
            return "Task request timed out.";
        default:
            return "Task generation failed.";
    }
}
