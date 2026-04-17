import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import type * as vscode from "vscode";

import type { CodingTaskSessionHandlers, TailEventsApi } from "./api-client";
import { toWorkspaceRelativePath } from "./path-utils";
import type {
    ApiResult,
    BackendEntityExplanation,
    BackendExplanationStreamInit,
    BackendTailEvent,
    BackendTaskStepEvent,
    BackendToolCallPayload,
    CodeTaskStatus,
    CodeViewModel,
    CodingTaskDraftResult,
    CodingTaskToolResultPayload,
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
const READY_TO_RUN_MESSAGE = "Ready to run a backend-orchestrated coding task.";
const NO_ACTIVE_EDITOR_MESSAGE = "No active editor.";
const UNSAVED_FILE_MESSAGE = "Only saved Python files are supported.";
const NON_FILE_MESSAGE = "Only local files are supported.";
const NON_PYTHON_FILE_MESSAGE = "Only Python files are supported.";
const OUTSIDE_WORKSPACE_MESSAGE = "The active file must be inside the current workspace.";
const EMPTY_PROMPT_MESSAGE = "Prompt is required.";
const TOO_MANY_CONTEXT_FILES_MESSAGE = "You can select at most 2 context files.";
const DUPLICATE_CONTEXT_FILE_MESSAGE = "Context files must not contain duplicates.";
const CONTEXT_TARGET_CONFLICT_MESSAGE = "Context files must not include the target file.";
const TASK_RUNNING_MESSAGE = "Task running. Waiting for verified draft...";
const TASK_READY_MESSAGE = "Verified draft ready. Apply is available.";
const TASK_CANCELLED_MESSAGE = "Task cancelled.";
const APPLYING_MESSAGE = "Applying verified draft and writing a TailEvent...";
const APPLY_SUCCESS_MESSAGE = "File updated and event written.";
const APPLY_SUCCESS_NO_ENTITY_MESSAGE = "File updated and event written. Re-run explain if needed.";
const FILE_CHANGED_MESSAGE = "The file changed after generation. Please run again.";
const APPLY_FAILED_MESSAGE = "Failed to apply the verified draft. Please run again.";

interface SidebarRuntime {
    getActiveEditor: () => vscode.TextEditor | null;
    getWorkspaceFolders: () => readonly vscode.WorkspaceFolder[] | undefined;
    resolveWorkspaceRelativePath?: (absolutePath: string) => string | null;
    resolveAbsoluteWorkspacePath?: (workspaceFilePath: string) => string | null;
    getOpenDocumentByAbsolutePath?: (absolutePath: string) => vscode.TextDocument | null;
    readFileText?: (absolutePath: string) => Promise<string>;
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
}

interface EditorContext {
    editor: vscode.TextEditor;
    absolutePath: string;
    workspaceFilePath: string;
    version: number;
    content: string;
    lineNumber: number;
}

interface CodeAvailability {
    context: EditorContext | null;
    filePath: string | null;
    reason: string | null;
}

interface ContextFileSelection {
    workspaceFilePath: string;
    absolutePath: string;
}

interface TaskContext {
    taskId: string | null;
    absolutePath: string;
    workspaceFilePath: string;
    documentVersion: number;
    lineNumber: number;
    originalContent: string;
}

export class TailEventsSidebarProvider implements vscode.WebviewViewProvider {
    private readonly apiClient: TailEventsApi;

    private readonly template: string;

    private readonly getBaseUrl: () => string;

    private readonly runtime: SidebarRuntime;

    private view: vscode.WebviewView | null = null;

    private currentEntityId: string | null = null;

    private currentMode: SidebarMode = "explain";

    private currentAbortController: AbortController | null = null;

    private currentTaskAbortController: AbortController | null = null;

    private currentTaskContext: TaskContext | null = null;

    private currentTaskResult: CodingTaskDraftResult | null = null;

    private codeStatus: CodeTaskStatus = "idle";

    private codeTranscriptText = "";

    private codeModelOutputText = "";

    private codeDraftText = "";

    private codeMessage: string | null = null;

    private codeModelAttempt = 0;

    private lastExplainState: SidebarMessageToWebview = {
        type: "state:empty",
        message: EMPTY_MESSAGE,
    };

    public constructor(options: SidebarProviderOptions) {
        this.apiClient = options.apiClient;
        this.getBaseUrl = options.getBaseUrl;
        this.runtime = options.runtime;
        this.template = readFileSync(options.templatePath, "utf8");
    }

    public getCurrentEntityId(): string | null {
        return this.currentEntityId;
    }

    public async showExplainEntity(entityId: string): Promise<void> {
        await this.setMode("explain");
        await this.loadEntity(entityId);
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

        let viewModel: SidebarViewModel | null = null;
        let eventsResult: ApiResult<BackendTailEvent[]> | null = null;
        let receivedInit = false;

        const postCurrentView = async (): Promise<void> => {
            if (!viewModel || signal.aborted) {
                return;
            }
            this.lastExplainState = {
                type: "state:update",
                data: viewModel,
            };
            await this.postMessage(this.lastExplainState);
        };

        const applyTimeline = (): void => {
            if (!viewModel || !eventsResult) {
                return;
            }
            viewModel.timeline = eventsResult.ok ? buildTimeline(eventsResult.data) : [];
            viewModel.historyAvailable = eventsResult.ok;
            viewModel.historyLoading = false;
        };

        const streamPromise = this.apiClient.streamExplanation(
            entityId,
            {
                onInit: (payload) => {
                    receivedInit = true;
                    viewModel = buildInitialViewModel(payload);
                    applyTimeline();
                    void postCurrentView();
                },
                onDelta: (text) => {
                    if (!viewModel) {
                        return;
                    }
                    viewModel.detailedExplanation = `${viewModel.detailedExplanation ?? ""}${text}`;
                    void postCurrentView();
                },
                onDone: (explanation) => {
                    if (!viewModel) {
                        return;
                    }
                    mergeFinalExplanation(viewModel, explanation);
                    void postCurrentView();
                },
                onError: (message) => {
                    if (!viewModel) {
                        return;
                    }
                    viewModel.streamError = message;
                    void postCurrentView();
                },
            },
            signal,
        );

        const timelinePromise = this.apiClient.getEntityEvents(entityId, signal).then((result) => {
            eventsResult = result;
            applyTimeline();
            return postCurrentView();
        });

        const [streamResult] = await Promise.all([streamPromise, timelinePromise]);
        if (signal.aborted) {
            return;
        }

        if (!streamResult.ok && !receivedInit) {
            this.lastExplainState = {
                type: "state:error",
                error: streamResult.error,
                baseUrl: normalizeBaseUrl(this.getBaseUrl()),
            };
            await this.postMessage(this.lastExplainState);
            return;
        }
    }

    public async refreshCodeContext(): Promise<void> {
        await this.postCodeState();
    }

    public async setMode(mode: SidebarMode): Promise<void> {
        this.currentMode = mode;
        await this.postMessage({
            type: "mode:update",
            mode: this.currentMode,
        });
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
                await this.setMode(message.mode);
                return;
            case "openRelatedEntity":
                if (message.entityId) {
                    await this.showExplainEntity(message.entityId);
                }
                return;
            case "runTask":
                await this.runTask(message.prompt, message.contextFiles);
                return;
            case "cancelTask":
                await this.cancelTask();
                return;
            case "applyTask":
                await this.applyTask();
                return;
            default:
                return;
        }
    }

    private async runTask(prompt: string, contextFiles: string[]): Promise<void> {
        const trimmedPrompt = prompt.trim();
        if (!trimmedPrompt) {
            await this.setCodeState("error", EMPTY_PROMPT_MESSAGE);
            return;
        }

        const availability = this.getCodeAvailability();
        if (!availability.context) {
            await this.setCodeState("error", availability.reason ?? NO_ACTIVE_EDITOR_MESSAGE);
            return;
        }

        const contextSelections = this.resolveContextFiles(
            contextFiles,
            availability.context,
        );
        if (!contextSelections.ok) {
            await this.setCodeState("error", contextSelections.message);
            return;
        }

        this.currentTaskResult = null;
        this.currentTaskContext = {
            taskId: null,
            absolutePath: availability.context.absolutePath,
            workspaceFilePath: availability.context.workspaceFilePath,
            documentVersion: availability.context.version,
            lineNumber: availability.context.lineNumber,
            originalContent: availability.context.content,
        };
        this.currentTaskAbortController?.abort();
        const controller = new AbortController();
        this.currentTaskAbortController = controller;

        this.codeTranscriptText = "";
        this.codeModelOutputText = "";
        this.codeDraftText = "";
        this.codeModelAttempt = 0;
        await this.setCodeState("running", TASK_RUNNING_MESSAGE);

        const handlers: CodingTaskSessionHandlers = {
            onCreated: (taskId) => {
                if (!this.currentTaskContext) {
                    return;
                }
                this.currentTaskContext.taskId = taskId;
                this.appendTranscript(`task created: ${taskId}`);
            },
            onStatus: (status) => {
                this.appendTranscript(`status: ${status}`);
            },
            onStep: (step) => {
                if (step.step_kind === "edit" && step.status === "started") {
                    this.codeModelAttempt += 1;
                    this.appendModelOutput(`--- attempt ${this.codeModelAttempt} ---\n`);
                }
                this.appendTranscript(formatStepTranscript(step));
            },
            onModelDelta: (text) => {
                this.appendModelOutput(text);
            },
            onToolCall: async (toolCall) => {
                this.appendTranscript(`tool_call: ${toolCall.tool_name} ${toolCall.file_path}`);
                const result = await this.handleToolCall(
                    toolCall,
                    availability.context!,
                    contextSelections.contextFiles,
                );
                this.appendTranscript(`tool_result: ${toolCall.file_path}`);
                return result;
            },
            onResult: (result) => {
                this.currentTaskResult = result;
                this.codeDraftText = result.updated_file_content;
                this.appendTranscript("result: verified draft ready");
            },
        };

        const result = await this.apiClient.runCodingTaskSession(
            {
                target_file_path: availability.context.workspaceFilePath,
                target_file_version: availability.context.version,
                user_prompt: trimmedPrompt,
                context_files: contextSelections.contextFiles.map((item) => item.workspaceFilePath),
            },
            handlers,
            controller.signal,
        );

        if (this.currentTaskAbortController !== controller || controller.signal.aborted) {
            return;
        }
        this.currentTaskAbortController = null;

        if (!result.ok) {
            this.currentTaskResult = null;
            await this.setCodeState("error", formatTaskError(result));
            return;
        }

        const validationError = validateDraftResult(
            result.data,
            availability.context.content,
        );
        if (validationError) {
            this.currentTaskResult = null;
            await this.setCodeState("error", validationError);
            return;
        }

        this.currentTaskResult = result.data;
        this.codeDraftText = result.data.updated_file_content;
        await this.setCodeState("ready_to_apply", TASK_READY_MESSAGE);
    }

    private async cancelTask(): Promise<void> {
        const controller = this.currentTaskAbortController;
        this.currentTaskAbortController = null;
        controller?.abort();

        const taskId = this.currentTaskContext?.taskId;
        if (taskId) {
            void this.apiClient.cancelCodingTask(taskId);
        }

        this.currentTaskResult = null;
        this.codeDraftText = "";
        await this.setCodeState("idle", TASK_CANCELLED_MESSAGE);
    }

    private async applyTask(): Promise<void> {
        if (!this.currentTaskContext || !this.currentTaskResult) {
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const currentEditor = this.runtime.getActiveEditor();
        if (
            !currentEditor ||
            currentEditor.document.uri.scheme !== "file" ||
            currentEditor.document.uri.fsPath !== this.currentTaskContext.absolutePath
        ) {
            await this.setCodeState("error", FILE_CHANGED_MESSAGE);
            return;
        }

        if (currentEditor.document.version !== this.currentTaskContext.documentVersion) {
            await this.setCodeState("error", FILE_CHANGED_MESSAGE);
            return;
        }

        await this.setCodeState("applying", APPLYING_MESSAGE);

        const replaced = await this.runtime.replaceDocumentContent(
            currentEditor,
            this.currentTaskResult.updated_file_content,
        );
        if (!replaced) {
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const saved = await this.runtime.saveDocument(currentEditor.document);
        if (!saved) {
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const eventPayload: CreateRawEventPayload = {
            action_type: "modify",
            file_path: this.currentTaskContext.workspaceFilePath,
            code_snapshot: this.currentTaskResult.updated_file_content,
            intent: this.currentTaskResult.intent,
            reasoning: this.currentTaskResult.reasoning ?? null,
            decision_alternatives: null,
            session_id: this.currentTaskResult.session_id,
            agent_step_id: this.currentTaskResult.agent_step_id,
            line_range: null,
            external_refs: [],
        };

        const eventResult = await this.apiClient.createEvent(eventPayload);
        if (!eventResult.ok) {
            await this.setCodeState("error", formatTaskError(eventResult));
            return;
        }

        this.apiClient.clearSummaryCache();

        const refreshResult = await this.apiClient.getEntityByLocation(
            this.currentTaskContext.workspaceFilePath,
            this.currentTaskContext.lineNumber,
        );

        if (refreshResult.ok) {
            await this.loadEntity(refreshResult.data.entity_id);
            await this.setCodeState("applied", APPLY_SUCCESS_MESSAGE);
            return;
        }

        await this.setCodeState("applied", APPLY_SUCCESS_NO_ENTITY_MESSAGE);
    }

    private async handleToolCall(
        payload: BackendToolCallPayload,
        targetContext: EditorContext,
        contextFiles: ContextFileSelection[],
    ): Promise<CodingTaskToolResultPayload> {
        if (payload.tool_name !== "view_file") {
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                error: `Unsupported tool: ${payload.tool_name}`,
            };
        }

        const targetMatch = payload.file_path === targetContext.workspaceFilePath;
        const contextMatch = contextFiles.find((item) => item.workspaceFilePath === payload.file_path);

        const absolutePath = targetMatch
            ? targetContext.absolutePath
            : contextMatch?.absolutePath ?? this.resolveAbsoluteWorkspacePath(payload.file_path);
        if (!absolutePath) {
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                error: `Context file not found in the current workspace: ${payload.file_path}`,
            };
        }

        try {
            const openDocument = this.runtime.getOpenDocumentByAbsolutePath?.(absolutePath) ?? null;
            const document = targetMatch
                ? targetContext.editor.document
                : openDocument;
            const content = document
                ? document.getText()
                : await this.readFileText(absolutePath);
            const contentHash = hashContent(content);
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                document_version: document?.version ?? null,
                content,
                content_hash: contentHash,
                error: null,
            };
        } catch (error) {
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                error: formatUnknownError(error),
            };
        }
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

        const workspaceFilePath = this.resolveWorkspaceRelativePath(document.uri.fsPath);
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
                lineNumber: editor.selection.active.line + 1,
            },
            filePath: document.uri.fsPath,
            reason: null,
        };
    }

    private resolveContextFiles(
        rawContextFiles: string[],
        targetContext: EditorContext,
    ): { ok: true; contextFiles: ContextFileSelection[] } | { ok: false; message: string } {
        const trimmed = rawContextFiles
            .map((item) => item.trim())
            .filter((item) => item.length > 0);
        if (trimmed.length > 2) {
            return { ok: false, message: TOO_MANY_CONTEXT_FILES_MESSAGE };
        }

        const unique = new Set(trimmed);
        if (unique.size !== trimmed.length) {
            return { ok: false, message: DUPLICATE_CONTEXT_FILE_MESSAGE };
        }

        const contextFiles: ContextFileSelection[] = [];
        for (const workspaceFilePath of trimmed) {
            if (workspaceFilePath === targetContext.workspaceFilePath) {
                return { ok: false, message: CONTEXT_TARGET_CONFLICT_MESSAGE };
            }
            const absolutePath = this.resolveAbsoluteWorkspacePath(workspaceFilePath);
            if (!absolutePath) {
                return {
                    ok: false,
                    message: `Context file not found in the current workspace: ${workspaceFilePath}`,
                };
            }
            contextFiles.push({
                workspaceFilePath,
                absolutePath,
            });
        }

        return { ok: true, contextFiles };
    }

    private resolveWorkspaceRelativePath(absolutePath: string): string | null {
        if (this.runtime.resolveWorkspaceRelativePath) {
            return this.runtime.resolveWorkspaceRelativePath(absolutePath);
        }
        return toWorkspaceRelativePath(absolutePath, this.runtime.getWorkspaceFolders());
    }

    private resolveAbsoluteWorkspacePath(workspaceFilePath: string): string | null {
        if (this.runtime.resolveAbsoluteWorkspacePath) {
            return this.runtime.resolveAbsoluteWorkspacePath(workspaceFilePath);
        }

        const workspaceFolders = this.runtime.getWorkspaceFolders();
        if (!workspaceFolders || workspaceFolders.length === 0) {
            return null;
        }

        for (const folder of workspaceFolders) {
            const candidate = path.join(folder.uri.fsPath, workspaceFilePath);
            const relativePath = this.resolveWorkspaceRelativePath(candidate);
            if (relativePath === workspaceFilePath) {
                return candidate;
            }
        }
        return null;
    }

    private async readFileText(absolutePath: string): Promise<string> {
        if (this.runtime.readFileText) {
            return this.runtime.readFileText(absolutePath);
        }
        throw new Error("File reading is not available in the current runtime.");
    }

    private appendTranscript(line: string): void {
        if (!line) {
            return;
        }
        this.codeTranscriptText = this.codeTranscriptText
            ? `${this.codeTranscriptText}\n${line}`
            : line;
        void this.postCodeState();
    }

    private appendModelOutput(text: string): void {
        if (!text) {
            return;
        }
        this.codeModelOutputText += text;
        void this.postCodeState();
    }

    private async setCodeState(status: CodeTaskStatus, message?: string | null): Promise<void> {
        this.codeStatus = status;
        this.codeMessage = message ?? defaultCodeMessage(status);
        await this.postCodeState();
    }

    private async postCodeState(): Promise<void> {
        const availability = this.getCodeAvailability();
        const data: CodeViewModel = {
            filePath: availability.filePath,
            status: this.codeStatus,
            transcriptText: this.codeTranscriptText,
            modelOutputText: this.codeModelOutputText,
            draftText: this.codeDraftText,
            message: this.codeMessage ?? defaultCodeMessage(this.codeStatus),
            canRun:
                availability.context !== null &&
                this.codeStatus !== "running" &&
                this.codeStatus !== "applying",
            canCancel: this.codeStatus === "running",
            canApply: this.codeStatus === "ready_to_apply" && this.currentTaskResult !== null,
        };
        await this.postMessage({
            type: "code:update",
            data,
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

function buildInitialViewModel(payload: BackendExplanationStreamInit): SidebarViewModel {
    const [lineStart, lineEnd] = payload.line_range ?? [null, null];
    const summary = typeof payload.summary === "string" && payload.summary.trim().length > 0
        ? payload.summary
        : null;

    return {
        entityId: payload.entity_id,
        entityName: payload.entity_name,
        entityType: payload.entity_type,
        signature: payload.signature ?? null,
        filePath: payload.file_path,
        lineStart,
        lineEnd,
        eventCount: payload.event_count,
        summary,
        summaryPending: summary === null,
        detailedExplanation: null,
        streamError: null,
        timeline: [],
        historyAvailable: false,
        historyLoading: true,
        relatedEntities: [],
    };
}

function mergeFinalExplanation(
    viewModel: SidebarViewModel,
    explanation: BackendEntityExplanation,
): void {
    viewModel.entityName = explanation.entity_name || viewModel.entityName;
    viewModel.entityType = explanation.entity_type || viewModel.entityType;
    viewModel.signature = explanation.signature ?? viewModel.signature ?? null;
    viewModel.summary = explanation.summary || viewModel.summary;
    viewModel.summaryPending = false;
    viewModel.detailedExplanation =
        explanation.detailed_explanation ?? viewModel.detailedExplanation ?? null;
    viewModel.relatedEntities = buildRelatedEntities(explanation);
    viewModel.streamError = null;
}

function buildTimeline(events: BackendTailEvent[]): TimelineItemViewModel[] {
    return [...events]
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
            };
        });
}

function buildRelatedEntities(
    explanation: BackendEntityExplanation,
): RelatedEntityViewModel[] {
    return explanation.related_entities.map((item) => {
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
}

function validateDraftResult(
    result: CodingTaskDraftResult,
    originalContent: string,
): string | null {
    if (!result.updated_file_content.trim()) {
        return "Task returned an empty draft.";
    }
    if (result.updated_file_content === originalContent) {
        return "Task did not change the target file.";
    }
    if (!result.intent.trim()) {
        return "Task returned an empty intent.";
    }
    return null;
}

function formatTaskError(result: ApiResult<unknown>): string {
    if (result.ok) {
        return TASK_READY_MESSAGE;
    }
    if (typeof result.message === "string" && result.message.trim().length > 0) {
        return result.message.trim();
    }
    switch (result.error) {
        case "backend_unavailable":
            return "TailEvents backend is unavailable.";
        case "timeout":
            return "Task request timed out.";
        default:
            return "Task generation failed.";
    }
}

function defaultCodeMessage(status: CodeTaskStatus): string | null {
    switch (status) {
        case "idle":
            return READY_TO_RUN_MESSAGE;
        case "running":
            return TASK_RUNNING_MESSAGE;
        case "ready_to_apply":
            return TASK_READY_MESSAGE;
        case "applying":
            return APPLYING_MESSAGE;
        case "applied":
            return APPLY_SUCCESS_MESSAGE;
        case "error":
            return null;
        default:
            return READY_TO_RUN_MESSAGE;
    }
}

function formatStepTranscript(step: BackendTaskStepEvent): string {
    const summary = step.output_summary || step.reasoning_summary || step.input_summary || step.intent;
    return `${step.step_kind}/${step.status}: ${step.file_path} - ${summary ?? step.intent}`;
}

function normalizeBaseUrl(baseUrl: string): string {
    return baseUrl.trim().replace(/\/+$/, "");
}

function hashContent(content: string): string {
    return createHash("sha256").update(content, "utf8").digest("hex");
}

function formatUnknownError(error: unknown): string {
    if (error instanceof Error) {
        return error.message;
    }
    return String(error);
}
