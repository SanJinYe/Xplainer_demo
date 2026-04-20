import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import type * as vscode from "vscode";

import type { CodingTaskSessionHandlers, TailEventsApi } from "./api-client";
import { toWorkspaceRelativePath } from "./path-utils";
import {
    buildCapabilitySummary,
    ProfileStateStore,
    resolveCodeEffectiveProfile,
    resolveExplainEffectiveProfile,
} from "./profile-resolver";
import type {
    ApiResult,
    BackendCodingCapabilitiesResponse,
    BackendCodingTaskHistoryDetail,
    BackendCodingTaskHistoryItem,
    BackendEntityExplanation,
    BackendGlobalImpactPath,
    BackendExplanationStreamInit,
    BackendTailEvent,
    BackendTaskStepEvent,
    BackendToolCallPayload,
    AssistantFileChangeViewModel,
    AssistantResultPayload,
    AssistantTurnDetails,
    AssistantToolTraceItemViewModel,
    CodeExplainEntityViewModel,
    CodeConversationMessageViewModel,
    CodeConversationRunViewModel,
    CodeConversationViewModel,
    CodeHistoryFiltersViewModel,
    CodeHistoryPageViewModel,
    CodePickerKind,
    CodePickerViewModel,
    CodeTaskCardDraftFileViewModel,
    CodeTaskCardViewModel,
    CodeTaskStatus,
    CodeViewModel,
    DraftFileViewModel,
    EffectiveProfileViewModel,
    HistoryFilterStatus,
    CodingTaskHistoryDetailViewModel,
    CodingTaskHistoryItemViewModel,
    CodingTaskLaunchMode,
    CodingTaskRequestedCapability,
    CodingTaskDraftResult,
    CodingTaskToolResultPayload,
    MessageActionViewModel,
    RecentTaskSummaryViewModel,
    RelatedEntityViewModel,
    SidebarMessageFromWebview,
    SidebarMessageToWebview,
    SidebarMode,
    SidebarViewModel,
    TimelineItemViewModel,
    TargetSelectionMode,
} from "./types";
import {
    buildActiveDraftFileViewModels,
    buildHistoryDetailStepViewModels,
    buildHistoryDraftFileViewModels,
    formatHistoryStepSummary,
    isSafeWorkspaceFilePath,
} from "./webview-host/state-projection";

const EMPTY_MESSAGE =
    "No entity selected. Use the editor title button, the editor context menu, or View Details from hover.";
const READY_TO_RUN_MESSAGE = "Ready to run a backend-orchestrated coding task.";
const NO_ACTIVE_EDITOR_MESSAGE = "No active editor.";
const UNSAVED_FILE_MESSAGE = "Only saved Python files are supported.";
const NON_FILE_MESSAGE = "Only local files are supported.";
const NON_PYTHON_FILE_MESSAGE = "Only Python files are supported.";
const OUTSIDE_WORKSPACE_MESSAGE = "The active file must be inside the current workspace.";
const EMPTY_PROMPT_MESSAGE = "Prompt is required.";
const TARGET_REQUIRED_MESSAGE = "Select a target Python file before running the task.";
const TOO_MANY_CONTEXT_FILES_MESSAGE = "You can select at most 3 context files.";
const DUPLICATE_CONTEXT_FILE_MESSAGE = "Context files must not contain duplicates.";
const CONTEXT_TARGET_CONFLICT_MESSAGE = "Context files must not include the target file.";
const TOO_MANY_EDITABLE_FILES_MESSAGE = "You can select at most 1 editable file.";
const DUPLICATE_EDITABLE_FILE_MESSAGE = "Editable files must not contain duplicates.";
const EDITABLE_TARGET_CONFLICT_MESSAGE = "Editable files must not include the target file.";
const EDITABLE_CONTEXT_CONFLICT_MESSAGE = "Editable files must not overlap with context files.";
const TARGET_MISSING_MESSAGE = "The selected target file is no longer available in the current workspace.";
const WORKSPACE_PICKER_EMPTY_MESSAGE = "No eligible Python files were found in the current workspace.";
const TASK_RUNNING_MESSAGE = "Task running. Waiting for verified draft...";
const TASK_READY_MESSAGE = "Verified draft ready. Apply is available.";
const TASK_CANCELLED_MESSAGE = "Task cancelled.";
const APPLYING_MESSAGE = "Applying verified draft and writing a TailEvent...";
const APPLY_SUCCESS_MESSAGE = "File updated and event written.";
const APPLY_SUCCESS_NO_ENTITY_MESSAGE = "File updated and event written. Re-run explain if needed.";
const APPLY_FAILED_MESSAGE = "Failed to apply the verified draft. Please run again.";
const APPLY_HISTORY_FAILED_MESSAGE = "Files updated, but task apply confirmation failed.";
const APPLY_PENDING_MESSAGE = "Files updated. Event write is still pending.";
const APPLY_WITHOUT_EVENTS_MESSAGE = "Files updated. Some events could not be written.";
const HISTORY_REUSED_MESSAGE = "Prompt and context files copied from task history.";
const HISTORY_REPLAY_READY_MESSAGE = "Replay prepared. Review the prompt, then click Run.";
const HISTORY_REPLAY_MISSING_TARGET_MESSAGE = "Replay is unavailable because the target file is missing.";
const HISTORY_LOAD_FAILED_MESSAGE = "Failed to load recent task history.";
const BASELINE_ONLY_DISCLAIMER =
    "此解释基于已有代码的基线扫描，不是真实 agent 会话中的创建/修改历史";
const MIXED_DISCLAIMER = "此解释同时包含基线扫描与真实 agent 会话记录";

interface SidebarRuntime {
    getActiveEditor: () => vscode.TextEditor | null;
    getWorkspaceFolders: () => readonly vscode.WorkspaceFolder[] | undefined;
    resolveWorkspaceRelativePath?: (absolutePath: string) => string | null;
    resolveAbsoluteWorkspacePath?: (workspaceFilePath: string) => string | null;
    listWorkspacePythonFiles?: () => Promise<WorkspaceFileCandidate[]>;
    getOpenDocumentByAbsolutePath?: (absolutePath: string) => vscode.TextDocument | null;
    openWorkspaceDocument?: (workspaceFilePath: string) => Promise<vscode.TextDocument | null>;
    readFileText?: (absolutePath: string) => Promise<string>;
    applyVerifiedFiles?: (
        files: Array<{ workspaceFilePath: string; absolutePath: string; content: string }>,
    ) => Promise<boolean>;
    openWorkspaceFile?: (workspaceFilePath: string) => Promise<boolean>;
    openDiffView?: (workspaceFilePath: string, content: string) => Promise<boolean>;
    executeCommand?: (command: string) => Promise<unknown>;
}

interface SidebarProviderOptions {
    apiClient: TailEventsApi;
    templatePath: string;
    reactLocalResourceRoots?: readonly vscode.Uri[];
    getReactAssetUris?: (webview: vscode.Webview) => {
        scriptUri: string;
        styleUri: string;
    };
    shouldUseLegacyWebview?: () => boolean;
    getBaseUrl: () => string;
    profileStateStore?: ProfileStateStore;
    getCodeProfilePreferenceId?: () => string | null;
    getExplainProfilePreferenceId?: () => string | null;
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

interface WorkspaceFileCandidate {
    workspaceFilePath: string;
    absolutePath: string;
}

interface SelectedFileReference {
    workspaceFilePath: string;
    absolutePath: string;
}

interface ExplainEntityTargetState {
    available: boolean;
    entityId: string | null;
    entityName: string | null;
    filePath: string | null;
    canUseAsTarget: boolean;
}

interface CodeTargetState {
    filePath: string | null;
    reason: string | null;
    selectionMode: TargetSelectionMode;
    activeContext: EditorContext | null;
    targetContext: EditorContext | null;
    targetReference: SelectedFileReference | null;
}

interface TaskContext {
    taskId: string | null;
    absolutePath: string | null;
    workspaceFilePath: string | null;
    documentVersion: number | null;
    lineNumber: number | null;
    originalContent: string | null;
}

interface HistoryQueryState {
    limit: number;
    offset: number;
    status: HistoryFilterStatus;
    targetFilePath: string | null;
    targetQuery: string;
    total: number;
    hasMore: boolean;
    queryVersion: number;
}

export class TailEventsSidebarProvider implements vscode.WebviewViewProvider {
    private readonly apiClient: TailEventsApi;

    private readonly template: string;

    private readonly getBaseUrl: () => string;

    private readonly profileStateStore: ProfileStateStore;

    private readonly getCodeProfilePreferenceId: () => string | null;

    private readonly getExplainProfilePreferenceId: () => string | null;

    private readonly runtime: SidebarRuntime;

    private readonly reactLocalResourceRoots: readonly vscode.Uri[];

    private readonly shouldUseLegacyWebview: () => boolean;

    private readonly getReactAssetUris: ((webview: vscode.Webview) => {
        scriptUri: string;
        styleUri: string;
    }) | null;

    private view: vscode.WebviewView | null = null;

    private currentEntityId: string | null = null;

    private currentMode: SidebarMode = "explain";

    private currentAbortController: AbortController | null = null;

    private currentTaskAbortController: AbortController | null = null;

    private currentHistoryAbortController: AbortController | null = null;

    private currentHistoryTargetsAbortController: AbortController | null = null;

    private currentTaskContext: TaskContext | null = null;

    private currentTaskResult: CodingTaskDraftResult | null = null;

    private codeStatus: CodeTaskStatus = "idle";

    private codeTranscriptText = "";

    private codeModelOutputText = "";

    private codeDraftText = "";

    private codeTaskCards: CodeTaskCardViewModel[] = [];

    private recentTaskSummaries: RecentTaskSummaryViewModel[] = [];

    private codeMessage: string | null = null;

    private codeHistoryLoading = false;

    private codeHistoryError: string | null = null;

    private codeHistoryNotice: string | null = null;

    private codeHistoryItems: CodingTaskHistoryItemViewModel[] = [];

    private codeHistoryDetail: CodingTaskHistoryDetailViewModel | null = null;

    private selectedHistoryTaskId: string | null = null;

    private pendingReplaySourceTaskId: string | null = null;

    private currentTaskLaunchMode: CodingTaskLaunchMode = "new";

    private currentTaskSourceTaskId: string | null = null;

    private overrideCodeProfileId: string | null = null;

    private requestedCapabilities: CodingTaskRequestedCapability[] = [];

    private targetSelectionMode: TargetSelectionMode = "follow_active";

    private selectedTargetFilePath: string | null = null;

    private lastFollowablePythonTarget: string | null = null;

    private selectedContextFiles: string[] = [];

    private selectedEditableFiles: string[] = [];

    private openCodePicker: CodePickerKind | null = null;

    private pickerSearch: Record<CodePickerKind, string> = {
        target: "",
        context: "",
        editable: "",
    };

    private draftTargetFilePath: string | null = null;

    private draftContextFiles: string[] = [];

    private draftEditableFiles: string[] = [];

    private workspacePythonFiles: WorkspaceFileCandidate[] = [];

    private historyTargetSuggestions: string[] = [];

    private historyTargetSuggestionsLoading = false;

    private historyQueryState: HistoryQueryState = {
        limit: 20,
        offset: 0,
        status: "all",
        targetFilePath: null,
        targetQuery: "",
        total: 0,
        hasMore: false,
        queryVersion: 0,
    };

    private codeModelAttempt = 0;

    private currentConversationRunId: string | null = null;

    private currentThinkingCardId: string | null = null;

    private currentLatestEditCardId: string | null = null;

    private nextTaskCardOrdinal = 0;

    private lastExplainState: SidebarMessageToWebview = {
        type: "state:empty",
        message: EMPTY_MESSAGE,
    };

    public constructor(options: SidebarProviderOptions) {
        this.apiClient = options.apiClient;
        this.getBaseUrl = options.getBaseUrl;
        this.profileStateStore = options.profileStateStore ?? new ProfileStateStore(options.apiClient);
        this.getCodeProfilePreferenceId = options.getCodeProfilePreferenceId ?? (() => null);
        this.getExplainProfilePreferenceId = options.getExplainProfilePreferenceId ?? (() => null);
        this.runtime = options.runtime;
        this.reactLocalResourceRoots = options.reactLocalResourceRoots ?? [];
        this.shouldUseLegacyWebview = options.shouldUseLegacyWebview ?? (() => true);
        this.getReactAssetUris = options.getReactAssetUris ?? null;
        this.template = readFileSync(options.templatePath, "utf8");
    }

    public getCurrentEntityId(): string | null {
        return this.currentEntityId;
    }

    public async showExplainEntity(entityId: string): Promise<void> {
        await this.setMode("explain");
        await this.loadEntity(entityId);
    }

    public async refreshAfterProfileChange(options?: { reloadExplain?: boolean }): Promise<void> {
        await this.profileStateStore.refresh();
        await this.postCodeState();
        if (options?.reloadExplain && this.currentEntityId) {
            await this.loadEntity(this.currentEntityId);
        }
    }

    public resolveWebviewView(webviewView: vscode.WebviewView): void {
        this.view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: this.reactLocalResourceRoots.length > 0
                ? [...this.reactLocalResourceRoots]
                : undefined,
        };
        webviewView.webview.html = this.renderHtml(webviewView.webview);
        webviewView.webview.onDidReceiveMessage((message: SidebarMessageFromWebview) => {
            void this.handleMessage(message);
        });
    }

    public refreshWebviewHtml(): void {
        if (!this.view) {
            return;
        }
        this.view.webview.html = this.renderHtml(this.view.webview);
    }

    public async loadEntity(entityId: string): Promise<void> {
        const signal = this.cancelPendingExplain();
        await this.profileStateStore.ensureLoaded(signal);
        const explainProfile = this.getExplainEffectiveProfile();
        if (!explainProfile.available) {
            this.currentEntityId = entityId;
            this.lastExplainState = {
                type: "state:empty",
                message: explainProfile.reason ?? "Explain profile is not available.",
            };
            await this.postMessage(this.lastExplainState);
            return;
        }
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
                    viewModel = buildInitialViewModel(payload, explainProfile);
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
            explainProfile.resolvedProfileId ?? undefined,
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
        this.captureFollowableTargetFromActiveEditor();
        if (this.targetSelectionMode === "follow_active") {
            const targetPath = this.getCodeTargetState().targetReference?.workspaceFilePath ?? null;
            const notice = this.pruneSelectionsForTarget(targetPath);
            if (notice) {
                this.codeHistoryNotice = notice;
            }
        }
        await this.postCodeState();
    }

    public async setMode(mode: SidebarMode): Promise<void> {
        this.currentMode = mode;
        await this.profileStateStore.ensureLoaded();
        if (mode === "code") {
            this.captureFollowableTargetFromActiveEditor();
        } else {
            this.resetAllPickerDrafts();
        }
        await this.postMessage({
            type: "mode:update",
            mode: this.currentMode,
        });
        await this.postCodeState();
        if (mode === "code") {
            void this.refreshHistory();
        }
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
                await this.profileStateStore.refresh();
                this.captureFollowableTargetFromActiveEditor();
                await this.postMessage({
                    type: "mode:update",
                    mode: this.currentMode,
                });
                await this.postCodeState();
                void this.refreshHistory();
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
            case "openWorkspaceFile":
                await this.openWorkspaceFile(message.path);
                return;
            case "openDiffView":
                await this.openDiffView(message.path);
                return;
            case "clearCodeConversation":
                this.clearCodeConversation();
                await this.postCodeState();
                return;
            case "setCodePickerOpen":
                await this.setCodePickerOpen(message.kind, message.open);
                return;
            case "setCodePickerSearch":
                this.setCodePickerSearch(message.kind, message.search);
                await this.postCodeState();
                return;
            case "setTargetPickerSelection":
                this.setTargetPickerSelection(message.path);
                await this.postCodeState();
                return;
            case "useActiveTargetFile":
                await this.useActiveTargetFile();
                return;
            case "useExplainFileAsTarget":
                await this.useExplainFileAsTarget();
                return;
            case "backToExplainEntity":
                await this.backToExplainEntity();
                return;
            case "toggleCodePickerSelection":
                this.toggleCodePickerSelection(message.kind, message.path, message.selected);
                await this.postCodeState();
                return;
            case "applyCodePickerSelection":
                await this.applyCodePickerSelection(message.kind);
                return;
            case "cancelCodePickerSelection":
                await this.cancelCodePickerSelection(message.kind);
                return;
            case "removeSelectedFile":
                this.removeSelectedFile(message.kind, message.path);
                await this.postCodeState();
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
            case "selectHistoryTask":
                this.selectedHistoryTaskId = message.taskId;
                await this.refreshHistory(message.taskId);
                return;
            case "reuseHistoryTask":
                await this.reuseHistoryTask(message.taskId);
                return;
            case "replayHistoryTask":
                await this.replayHistoryTask(message.taskId);
                return;
            case "selectCodeProfile":
                await this.runtime.executeCommand?.("tailEvents.selectCodeProfile");
                return;
            case "selectExplainProfile":
                await this.runtime.executeCommand?.("tailEvents.selectExplainProfile");
                return;
            case "onboardRepository":
                await this.runtime.executeCommand?.("tailEvents.onboardRepository");
                return;
            case "setHistoryStatusFilter":
                await this.setHistoryStatusFilter(message.status);
                return;
            case "setHistoryTargetQuery":
                await this.setHistoryTargetQuery(message.query);
                return;
            case "setHistoryTargetSelection":
                await this.setHistoryTargetSelection(message.targetFilePath);
                return;
            case "loadMoreHistory":
                await this.loadMoreHistory();
                return;
            default:
                return;
        }
    }

    private async setCodePickerOpen(kind: CodePickerKind, open: boolean): Promise<void> {
        if (!open) {
            await this.cancelCodePickerSelection(kind);
            return;
        }

        await this.refreshWorkspacePythonFiles();
        const targetState = this.getCodeTargetState();
        this.pickerSearch[kind] = "";
        const hasCandidates = this.getEligiblePickerCandidateCount(kind, targetState) > 0;
        if (!hasCandidates) {
            this.codeHistoryNotice = WORKSPACE_PICKER_EMPTY_MESSAGE;
            await this.postCodeState();
            return;
        }

        this.resetAllPickerDrafts();
        this.openCodePicker = kind;
        if (kind === "target") {
            this.draftTargetFilePath = targetState.filePath;
        } else if (kind === "context") {
            this.draftContextFiles = [...this.selectedContextFiles];
        } else {
            this.draftEditableFiles = [...this.selectedEditableFiles];
        }
        await this.postCodeState();
    }

    private setCodePickerSearch(kind: CodePickerKind, search: string): void {
        this.pickerSearch[kind] = search;
    }

    private setTargetPickerSelection(workspaceFilePath: string): void {
        this.draftTargetFilePath = workspaceFilePath;
    }

    private toggleCodePickerSelection(
        kind: "context" | "editable",
        workspaceFilePath: string,
        selected: boolean,
    ): void {
        if (kind === "context") {
            if (selected) {
                if (this.draftContextFiles.includes(workspaceFilePath)) {
                    return;
                }
                if (this.draftContextFiles.length >= 3) {
                    this.codeHistoryNotice = TOO_MANY_CONTEXT_FILES_MESSAGE;
                    return;
                }
                this.draftContextFiles = [...this.draftContextFiles, workspaceFilePath];
                this.codeHistoryNotice = null;
                return;
            }
            this.draftContextFiles = this.draftContextFiles.filter((item) => item !== workspaceFilePath);
            return;
        }

        if (selected) {
            if (this.draftEditableFiles.includes(workspaceFilePath)) {
                return;
            }
            if (this.draftEditableFiles.length >= 1) {
                this.codeHistoryNotice = TOO_MANY_EDITABLE_FILES_MESSAGE;
                return;
            }
            this.draftEditableFiles = [...this.draftEditableFiles, workspaceFilePath];
            this.codeHistoryNotice = null;
            return;
        }
        this.draftEditableFiles = this.draftEditableFiles.filter((item) => item !== workspaceFilePath);
    }

    private async applyCodePickerSelection(kind: CodePickerKind): Promise<void> {
        const targetState = this.getCodeTargetState();
        if (kind === "target") {
            const targetFilePath = this.draftTargetFilePath;
            if (!targetFilePath) {
                this.codeHistoryNotice = TARGET_REQUIRED_MESSAGE;
                await this.postCodeState();
                return;
            }
            await this.applyExplicitTargetSelection(targetFilePath);
            return;
        }

        if (kind === "context") {
            const sanitizedContext = this.sanitizeSelectionPaths(this.draftContextFiles, {
                kind: "context",
                max: 3,
                targetFilePath: targetState.targetReference?.workspaceFilePath ?? targetState.filePath,
                blockedPaths: this.selectedEditableFiles,
            });
            this.selectedContextFiles = sanitizedContext.accepted;
            this.codeHistoryNotice = sanitizedContext.skipped.length > 0
                ? this.buildHistoryNotice("Context selection updated.", sanitizedContext.skipped)
                : null;
            await this.cancelCodePickerSelection(kind, { keepNotice: true });
            return;
        }

        const sanitizedEditable = this.sanitizeSelectionPaths(this.draftEditableFiles, {
            kind: "editable",
            max: 1,
            targetFilePath: targetState.targetReference?.workspaceFilePath ?? targetState.filePath,
            blockedPaths: this.selectedContextFiles,
        });
        this.selectedEditableFiles = sanitizedEditable.accepted;
        this.codeHistoryNotice = sanitizedEditable.skipped.length > 0
            ? this.buildHistoryNotice("Editable selection updated.", sanitizedEditable.skipped)
            : null;
        await this.cancelCodePickerSelection(kind, { keepNotice: true });
    }

    private async cancelCodePickerSelection(
        kind: CodePickerKind,
        options?: { keepNotice?: boolean },
    ): Promise<void> {
        if (this.openCodePicker === kind) {
            this.openCodePicker = null;
        }
        this.pickerSearch[kind] = "";
        if (kind === "target") {
            this.draftTargetFilePath = null;
        } else if (kind === "context") {
            this.draftContextFiles = [];
        } else {
            this.draftEditableFiles = [];
        }
        if (!options?.keepNotice) {
            this.codeHistoryNotice = null;
        }
        await this.postCodeState();
    }

    private async useActiveTargetFile(): Promise<void> {
        this.resetAllPickerDrafts();
        this.targetSelectionMode = "follow_active";
        this.selectedTargetFilePath = null;
        this.captureFollowableTargetFromActiveEditor();
        const targetPath = this.getCodeTargetState().targetReference?.workspaceFilePath ?? null;
        const notice = this.pruneSelectionsForTarget(targetPath);
        this.codeHistoryNotice = notice;
        await this.postCodeState();
    }

    private async useExplainFileAsTarget(): Promise<void> {
        const explainEntity = this.getCurrentExplainEntityTargetState();
        if (!explainEntity.canUseAsTarget || !explainEntity.filePath) {
            return;
        }
        await this.applyExplicitTargetSelection(explainEntity.filePath);
    }

    private async backToExplainEntity(): Promise<void> {
        const explainEntity = this.getCurrentExplainEntityTargetState();
        if (!explainEntity.available || !explainEntity.entityId) {
            return;
        }
        this.resetAllPickerDrafts();
        await this.showExplainEntity(explainEntity.entityId);
    }

    private removeSelectedFile(kind: "context" | "editable", workspaceFilePath: string): void {
        if (kind === "context") {
            this.selectedContextFiles = this.selectedContextFiles.filter((item) => {
                return item !== workspaceFilePath;
            });
            return;
        }
        this.selectedEditableFiles = this.selectedEditableFiles.filter((item) => {
            return item !== workspaceFilePath;
        });
    }

    private async runTask(prompt: string): Promise<void> {
        const trimmedPrompt = prompt.trim();
        if (!trimmedPrompt) {
            await this.setCodeState("error", EMPTY_PROMPT_MESSAGE);
            return;
        }

        this.captureFollowableTargetFromActiveEditor();
        const targetState = this.getCodeTargetState();
        const selectionResult = this.resolveRunSelections(targetState);
        if (!selectionResult.ok) {
            await this.setCodeState("error", selectionResult.message);
            return;
        }

        const taskContext = await this.buildTaskContext(targetState);
        if (!taskContext) {
            await this.setCodeState("error", TARGET_MISSING_MESSAGE);
            return;
        }
        const editablePayload = await this.buildEditablePayload(selectionResult.editableFiles);
        if (!editablePayload) {
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }
        const codeProfile = this.getCodeEffectiveProfile();
        if (!codeProfile.available) {
            await this.setCodeState(
                "error",
                codeProfile.reason ?? "Code profile is not available.",
            );
            return;
        }

        this.currentTaskResult = null;
        this.currentTaskContext = taskContext;
        this.currentTaskAbortController?.abort();
        const controller = new AbortController();
        this.currentTaskAbortController = controller;

        this.codeTranscriptText = "";
        this.codeModelOutputText = "";
        this.codeDraftText = "";
        this.codeModelAttempt = 0;
        this.codeHistoryNotice = null;
        const pendingReplaySourceTaskId = this.pendingReplaySourceTaskId;
        this.currentTaskLaunchMode = pendingReplaySourceTaskId ? "replay" : "new";
        this.currentTaskSourceTaskId = pendingReplaySourceTaskId;
        this.beginConversationRun(trimmedPrompt, targetState);
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
                this.finalizeThinkingCard();
                if (step.step_kind === "edit" && step.status === "started") {
                    this.codeModelAttempt += 1;
                    this.appendModelOutput(`--- attempt ${this.codeModelAttempt} ---\n`);
                }
                this.appendStepCard(step);
                this.appendTranscript(formatStepTranscript(step));
            },
            onModelDelta: (text) => {
                this.appendThinkingDelta(text);
                this.appendModelOutput(text);
            },
            onToolCall: async (toolCall) => {
                this.appendTranscript(
                    `tool_call: ${toolCall.tool_name} ${toolCall.file_path ?? toolCall.query ?? ""}`.trim(),
                );
                const result = await this.handleToolCall(
                    toolCall,
                    targetState,
                    selectionResult.contextFiles,
                    selectionResult.editableFiles,
                );
                this.appendTranscript(
                    `tool_result: ${toolCall.file_path ?? toolCall.query ?? toolCall.tool_name}`.trim(),
                );
                return result;
            },
            onResult: (result) => {
                this.currentTaskResult = result;
                this.codeDraftText = resolveDraftText(result);
                this.finalizeThinkingCard();
                void this.attachDraftFilesToConversation();
                this.appendTranscript("result: verified draft ready");
            },
        };

        const result = await this.apiClient.runCodingTaskSession(
            {
                target_file_path: taskContext.workspaceFilePath,
                target_file_version: taskContext.documentVersion,
                user_prompt: trimmedPrompt,
                context_files: selectionResult.contextFiles.map((item) => item.workspaceFilePath),
                editable_files: editablePayload,
                launch_mode: pendingReplaySourceTaskId ? "replay" : "new",
                source_task_id: pendingReplaySourceTaskId,
                selected_profile_id: codeProfile.resolvedProfileId ?? null,
                requested_capabilities: this.requestedCapabilities,
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
            this.finalizeThinkingCard();
            const errorMessage = formatTaskError(result);
            this.pushErrorCard(errorMessage, result.message ?? errorMessage);
            await this.setCodeState("error", errorMessage);
            await this.refreshHistory(this.selectedHistoryTaskId);
            return;
        }

        const validationError = validateDraftResult(
            result.data,
            taskContext.workspaceFilePath,
            taskContext.originalContent,
        );
        if (validationError) {
            this.currentTaskResult = null;
            this.finalizeThinkingCard();
            this.pushErrorCard(validationError);
            await this.setCodeState("error", validationError);
            await this.refreshHistory(this.selectedHistoryTaskId);
            return;
        }

        this.currentTaskResult = result.data;
        this.pendingReplaySourceTaskId = null;
        this.codeDraftText = resolveDraftText(result.data);
        await this.attachDraftFilesToConversation();
        await this.pushDraftReadyCard(result.data.task_id);
        await this.setCodeState("ready_to_apply", TASK_READY_MESSAGE);
        await this.refreshHistory(result.data.task_id);
    }

    private async cancelTask(): Promise<void> {
        const controller = this.currentTaskAbortController;
        this.currentTaskAbortController = null;
        controller?.abort();
        this.finalizeThinkingCard();

        const taskId = this.currentTaskContext?.taskId;
        if (taskId) {
            await this.apiClient.cancelCodingTask(taskId);
        }

        this.currentTaskResult = null;
        this.codeDraftText = "";
        this.pushErrorCard(TASK_CANCELLED_MESSAGE);
        await this.setCodeState("idle", TASK_CANCELLED_MESSAGE);
        await this.refreshHistory(taskId ?? this.selectedHistoryTaskId);
    }

    private async applyTask(): Promise<void> {
        if (!this.currentTaskContext || !this.currentTaskResult) {
            this.pushErrorCard(APPLY_FAILED_MESSAGE);
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const verifiedFiles = this.currentTaskResult.verified_files ?? [];
        if (verifiedFiles.length === 0) {
            this.pushErrorCard(APPLY_FAILED_MESSAGE);
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const precheckMessage = await this.precheckVerifiedFiles(verifiedFiles);
        if (precheckMessage) {
            this.currentTaskResult = null;
            this.pushErrorCard(precheckMessage);
            await this.setCodeState("error", precheckMessage);
            return;
        }

        await this.setCodeState("applying", APPLYING_MESSAGE);

        const filesToApply = verifiedFiles
            .map((item) => {
                const absolutePath =
                    item.file_path === this.currentTaskContext?.workspaceFilePath
                        ? this.currentTaskContext.absolutePath
                        : this.resolveAbsoluteWorkspacePath(item.file_path);
                if (!absolutePath) {
                    return null;
                }
                return {
                    workspaceFilePath: item.file_path,
                    absolutePath,
                    content: item.content,
                };
            })
            .filter((item): item is { workspaceFilePath: string; absolutePath: string; content: string } => {
                return item !== null;
            });
        if (filesToApply.length !== verifiedFiles.length) {
            this.pushErrorCard(APPLY_FAILED_MESSAGE);
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        const applied = await this.runtime.applyVerifiedFiles?.(filesToApply);
        if (!applied) {
            this.pushErrorCard(APPLY_FAILED_MESSAGE);
            await this.setCodeState("error", APPLY_FAILED_MESSAGE);
            return;
        }

        this.apiClient.clearSummaryCache();
        const appliedResult = await this.apiClient.markCodingTaskApplied(
            this.currentTaskResult.task_id,
            {
                applied_files: verifiedFiles.map((item) => ({
                    file_path: item.file_path,
                    content_hash: item.content_hash,
                })),
            },
        );
        await this.refreshHistory(this.currentTaskResult.task_id);

        if (!appliedResult.ok) {
            this.pushErrorCard(APPLY_HISTORY_FAILED_MESSAGE);
            await this.setCodeState("error", APPLY_HISTORY_FAILED_MESSAGE);
            return;
        }

        const appliedStatus = this.codeHistoryDetail?.status;
        if (appliedStatus === "applied_event_pending") {
            await this.setCodeState("applied", APPLY_PENDING_MESSAGE);
        } else if (appliedStatus === "applied_without_events") {
            await this.setCodeState("applied", APPLY_WITHOUT_EVENTS_MESSAGE);
        } else {
            await this.setCodeState("applied", APPLY_SUCCESS_MESSAGE);
        }
        this.markLatestDraftReadyApplied();

        if (this.shouldRefreshExplainAfterApply()) {
            const targetWorkspaceFilePath = this.currentTaskContext.workspaceFilePath;
            if (!targetWorkspaceFilePath) {
                await this.setCodeState("applied", APPLY_SUCCESS_NO_ENTITY_MESSAGE);
                return;
            }
            const refreshResult = await this.apiClient.getEntityByLocation(
                targetWorkspaceFilePath,
                this.currentTaskContext.lineNumber ?? 1,
            );
            if (refreshResult.ok) {
                await this.loadEntity(refreshResult.data.entity_id);
                return;
            }
        }

        if (appliedStatus !== "applied_event_pending" && appliedStatus !== "applied_without_events") {
            await this.setCodeState("applied", APPLY_SUCCESS_NO_ENTITY_MESSAGE);
        }
    }

    private async refreshHistory(
        preferredTaskId?: string | null,
        options?: { append?: boolean },
    ): Promise<void> {
        const append = options?.append === true;
        this.currentHistoryAbortController?.abort();
        const controller = new AbortController();
        this.currentHistoryAbortController = controller;
        const queryVersion = this.historyQueryState.queryVersion + 1;
        this.historyQueryState.queryVersion = queryVersion;
        this.codeHistoryLoading = true;
        this.codeHistoryError = null;
        await this.postCodeState();

        const requestOffset = append ? this.codeHistoryItems.length : 0;
        const historyResult = await this.apiClient.getCodingTaskHistory(
            {
                limit: this.historyQueryState.limit,
                offset: requestOffset,
                status:
                    this.historyQueryState.status === "all"
                        ? undefined
                        : this.historyQueryState.status,
                targetFilePath: this.historyQueryState.targetFilePath ?? undefined,
            },
            controller.signal,
        );
        const recentTasksResult = await this.apiClient.getCodingTaskHistory(
            {
                limit: 5,
                offset: 0,
            },
            controller.signal,
        );
        if (
            this.currentHistoryAbortController !== controller ||
            controller.signal.aborted ||
            this.historyQueryState.queryVersion !== queryVersion
        ) {
            return;
        }

        if (!historyResult.ok) {
            this.codeHistoryLoading = false;
            this.codeHistoryError = HISTORY_LOAD_FAILED_MESSAGE;
            await this.postCodeState();
            return;
        }

        this.recentTaskSummaries = buildRecentTaskSummaries(recentTasksResult);

        const historyPage = Array.isArray(historyResult.data)
            ? {
                items: historyResult.data,
                total: historyResult.data.length,
                limit: historyResult.data.length,
                offset: 0,
                has_more: false,
            }
            : historyResult.data;

        const mergedItems = append
            ? mergeHistoryItems(
                this.codeHistoryItems,
                historyPage.items,
            )
            : historyPage.items.map((item) => ({
                taskId: item.task_id,
                targetFilePath: item.target_file_path,
                userPrompt: item.user_prompt,
                status: item.status,
                createdAt: item.created_at,
                updatedAt: item.updated_at,
                selected: false,
            }));
        const selectedTaskId = this.resolveSelectedHistoryTaskId(
            mergedItems.map((item) => ({ task_id: item.taskId })),
            preferredTaskId,
        );
        this.selectedHistoryTaskId = selectedTaskId;
        this.codeHistoryItems = mergedItems.map((item) => ({
            ...item,
            selected: item.taskId === selectedTaskId,
        }));
        this.historyQueryState.offset = this.codeHistoryItems.length;
        this.historyQueryState.total = historyPage.total;
        this.historyQueryState.hasMore = historyPage.has_more;

        if (!selectedTaskId) {
            this.codeHistoryLoading = false;
            this.codeHistoryDetail = null;
            this.currentHistoryAbortController = null;
            await this.postCodeState();
            return;
        }

        if (
            append &&
            this.codeHistoryDetail &&
            this.codeHistoryDetail.taskId === selectedTaskId &&
            !preferredTaskId
        ) {
            this.codeHistoryLoading = false;
            this.currentHistoryAbortController = null;
            await this.postCodeState();
            return;
        }

        const detailResult = await this.apiClient.getCodingTaskHistoryDetail(
            selectedTaskId,
            controller.signal,
        );
        if (
            this.currentHistoryAbortController !== controller ||
            controller.signal.aborted ||
            this.historyQueryState.queryVersion !== queryVersion
        ) {
            return;
        }

        this.codeHistoryLoading = false;
        if (!detailResult.ok) {
            this.codeHistoryError = HISTORY_LOAD_FAILED_MESSAGE;
            this.codeHistoryDetail = null;
            this.currentHistoryAbortController = null;
            await this.postCodeState();
            return;
        }

        this.codeHistoryError = null;
        this.codeHistoryDetail = toHistoryDetailViewModel(detailResult.data);
        this.currentHistoryAbortController = null;
        await this.postCodeState();
    }

    private async setHistoryStatusFilter(status: HistoryFilterStatus): Promise<void> {
        this.historyQueryState.status = status;
        this.historyQueryState.offset = 0;
        await this.refreshHistory();
    }

    private async setHistoryTargetQuery(query: string): Promise<void> {
        this.historyQueryState.targetQuery = query;
        await this.loadHistoryTargetSuggestions(query);
        await this.postCodeState();
    }

    private async setHistoryTargetSelection(targetFilePath: string | null): Promise<void> {
        this.historyQueryState.targetFilePath = targetFilePath;
        this.historyQueryState.targetQuery = targetFilePath ?? "";
        if (!targetFilePath) {
            this.historyTargetSuggestions = [];
        }
        this.historyQueryState.offset = 0;
        await this.refreshHistory();
    }

    private async loadMoreHistory(): Promise<void> {
        if (this.codeHistoryLoading || !this.historyQueryState.hasMore) {
            return;
        }
        await this.refreshHistory(this.selectedHistoryTaskId, { append: true });
    }

    private async loadHistoryTargetSuggestions(query: string): Promise<void> {
        if (!this.apiClient.getCodingTaskHistoryTargets) {
            this.historyTargetSuggestions = [];
            this.historyTargetSuggestionsLoading = false;
            return;
        }
        this.currentHistoryTargetsAbortController?.abort();
        const controller = new AbortController();
        this.currentHistoryTargetsAbortController = controller;
        this.historyTargetSuggestionsLoading = true;
        await this.postCodeState();

        const result = await this.apiClient.getCodingTaskHistoryTargets(
            {
                query,
                limit: 10,
            },
            controller.signal,
        );
        if (
            this.currentHistoryTargetsAbortController !== controller ||
            controller.signal.aborted
        ) {
            return;
        }

        this.historyTargetSuggestionsLoading = false;
        this.currentHistoryTargetsAbortController = null;
        this.historyTargetSuggestions = result.ok ? result.data.items : [];
    }

    private async reuseHistoryTask(taskId: string): Promise<void> {
        this.resetAllPickerDrafts();
        const detail = this.codeHistoryDetail;
        if (!detail || detail.taskId !== taskId) {
            await this.refreshHistory(taskId);
        }
        const activeDetail = this.codeHistoryDetail;
        if (!activeDetail || activeDetail.taskId !== taskId) {
            return;
        }

        await this.postMessage({
            type: "code:fillPrompt",
            prompt: activeDetail.userPrompt,
        });
        const currentTargetPath = this.getCodeTargetState().targetReference?.workspaceFilePath ?? null;
        const sanitizedContext = this.sanitizeSelectionPaths(activeDetail.contextFiles, {
            kind: "context",
            max: 3,
            targetFilePath: currentTargetPath,
            blockedPaths: this.selectedEditableFiles,
        });
        this.selectedContextFiles = sanitizedContext.accepted;
        this.pendingReplaySourceTaskId = null;
        this.currentTaskLaunchMode = "new";
        this.currentTaskSourceTaskId = null;
        this.overrideCodeProfileId = activeDetail.selectedProfileId;
        this.requestedCapabilities = [...activeDetail.requestedCapabilities];
        this.codeHistoryNotice = this.buildHistoryNotice(
            HISTORY_REUSED_MESSAGE,
            sanitizedContext.skipped,
        );
        await this.postCodeState();
    }

    private async replayHistoryTask(taskId: string): Promise<void> {
        this.resetAllPickerDrafts();
        const detail = this.codeHistoryDetail;
        if (!detail || detail.taskId !== taskId) {
            await this.refreshHistory(taskId);
        }
        const activeDetail = this.codeHistoryDetail;
        if (!activeDetail || activeDetail.taskId !== taskId) {
            return;
        }

        const targetReference = this.resolveFileReference(activeDetail.targetFilePath);
        if (!targetReference) {
            this.codeHistoryNotice = HISTORY_REPLAY_MISSING_TARGET_MESSAGE;
            await this.postCodeState();
            return;
        }

        const sanitizedContext = this.sanitizeSelectionPaths(activeDetail.contextFiles, {
            kind: "context",
            max: 3,
            targetFilePath: activeDetail.targetFilePath,
            blockedPaths: [],
        });
        const sanitizedEditable = this.sanitizeSelectionPaths(activeDetail.editableFiles, {
            kind: "editable",
            max: 1,
            targetFilePath: activeDetail.targetFilePath,
            blockedPaths: sanitizedContext.accepted,
        });

        this.targetSelectionMode = "explicit";
        this.selectedTargetFilePath = activeDetail.targetFilePath;
        this.selectedContextFiles = sanitizedContext.accepted;
        this.selectedEditableFiles = sanitizedEditable.accepted;
        await this.runtime.openWorkspaceFile?.(targetReference.workspaceFilePath);
        await this.postMessage({
            type: "code:fillPrompt",
            prompt: activeDetail.userPrompt,
        });
        this.pendingReplaySourceTaskId = activeDetail.taskId;
        this.currentTaskLaunchMode = "replay";
        this.currentTaskSourceTaskId = activeDetail.taskId;
        this.overrideCodeProfileId = activeDetail.selectedProfileId;
        this.requestedCapabilities = [...activeDetail.requestedCapabilities];
        this.codeHistoryNotice = this.buildHistoryNotice(
            HISTORY_REPLAY_READY_MESSAGE,
            [...sanitizedContext.skipped, ...sanitizedEditable.skipped],
        );
        await this.postCodeState();
    }

    private async handleToolCall(
        payload: BackendToolCallPayload,
        targetState: CodeTargetState,
        contextFiles: SelectedFileReference[],
        editableFiles: SelectedFileReference[],
    ): Promise<CodingTaskToolResultPayload> {
        if (payload.tool_name === "search_workspace") {
            return this.handleSearchWorkspaceToolCall(payload);
        }

        if (payload.tool_name !== "view_file" || !payload.file_path) {
            return {
                call_id: payload.call_id,
                tool_name: payload.tool_name,
                file_path: payload.file_path,
                error: `Unsupported tool: ${payload.tool_name}`,
            };
        }

        const editableReference = [
            targetState.targetReference,
            ...editableFiles,
        ].find((item) => item?.workspaceFilePath === payload.file_path) ?? null;
        const contextReference =
            contextFiles.find((item) => item.workspaceFilePath === payload.file_path) ?? null;
        const matchedReference = editableReference ?? contextReference ?? this.resolveFileReference(payload.file_path);
        if (!matchedReference) {
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                error: `Context file not found in the current workspace: ${payload.file_path}`,
            };
        }

        try {
            if (editableReference) {
                const document = await this.openWorkspaceDocument(
                    matchedReference,
                    targetState.activeContext,
                );
                if (!document) {
                    return {
                        call_id: payload.call_id,
                        tool_name: "view_file",
                        file_path: payload.file_path,
                        error: `Context file not found in the current workspace: ${payload.file_path}`,
                    };
                }
                const content = document.getText();
                return {
                    call_id: payload.call_id,
                    tool_name: "view_file",
                    file_path: payload.file_path,
                    document_version: document.version,
                    content,
                    content_hash: hashContent(content),
                    error: null,
                };
            }

            const openDocument =
                this.runtime.getOpenDocumentByAbsolutePath?.(matchedReference.absolutePath) ?? null;
            const content = openDocument
                ? openDocument.getText()
                : await this.readFileText(matchedReference.absolutePath);
            return {
                call_id: payload.call_id,
                tool_name: "view_file",
                file_path: payload.file_path,
                document_version: openDocument?.version ?? null,
                content,
                content_hash: hashContent(content),
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

    private async handleSearchWorkspaceToolCall(
        payload: BackendToolCallPayload,
    ): Promise<CodingTaskToolResultPayload> {
        const query = payload.query?.trim() ?? "";
        const limit = Math.max(1, Math.min(payload.limit ?? 8, 20));
        try {
            const candidates = await this.listWorkspacePythonFiles();
            const matches = this.rankWorkspaceFileCandidates(candidates, query)
                .slice(0, limit)
                .map((item) => item.workspaceFilePath);
            return {
                call_id: payload.call_id,
                tool_name: "search_workspace",
                matches,
                error: null,
            };
        } catch (error) {
            return {
                call_id: payload.call_id,
                tool_name: "search_workspace",
                matches: [],
                error: formatUnknownError(error),
            };
        }
    }

    private rankWorkspaceFileCandidates(
        candidates: WorkspaceFileCandidate[],
        query: string,
    ): WorkspaceFileCandidate[] {
        const tokens = query
            .toLowerCase()
            .split(/[^a-z0-9_]+/)
            .map((item) => item.trim())
            .filter((item) => item.length >= 2);
        if (tokens.length === 0) {
            return [...candidates];
        }

        return [...candidates]
            .map((candidate) => {
                const normalizedPath = candidate.workspaceFilePath.toLowerCase();
                const fileName = path.basename(normalizedPath, ".py");
                let score = 0;
                for (const token of tokens) {
                    if (fileName === token) {
                        score += 6;
                    } else if (fileName.includes(token)) {
                        score += 4;
                    } else if (normalizedPath.includes(token)) {
                        score += 2;
                    }
                }
                return { candidate, score };
            })
            .filter((item) => item.score > 0)
            .sort((left, right) => {
                if (right.score !== left.score) {
                    return right.score - left.score;
                }
                return left.candidate.workspaceFilePath.localeCompare(right.candidate.workspaceFilePath);
            })
            .map((item) => item.candidate);
    }

    private getCurrentActiveEditorState(): {
        context: EditorContext | null;
        filePath: string | null;
        reason: string | null;
    } {
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

    private captureFollowableTargetFromActiveEditor(): EditorContext | null {
        const activeState = this.getCurrentActiveEditorState();
        if (activeState.context) {
            this.lastFollowablePythonTarget = activeState.context.workspaceFilePath;
            return activeState.context;
        }
        return null;
    }

    private getCodeTargetState(): CodeTargetState {
        const activeState = this.getCurrentActiveEditorState();
        if (activeState.context) {
            this.lastFollowablePythonTarget = activeState.context.workspaceFilePath;
        }

        if (this.targetSelectionMode === "explicit") {
            if (!this.selectedTargetFilePath) {
                return {
                    filePath: null,
                    reason: null,
                    selectionMode: "explicit",
                    activeContext: activeState.context,
                    targetContext: null,
                    targetReference: null,
                };
            }
            const targetReference = this.resolveFileReference(this.selectedTargetFilePath);
            return {
                filePath: this.selectedTargetFilePath,
                reason: targetReference ? null : TARGET_MISSING_MESSAGE,
                selectionMode: "explicit",
                activeContext: activeState.context,
                targetContext:
                    activeState.context?.workspaceFilePath === this.selectedTargetFilePath
                        ? activeState.context
                        : null,
                targetReference,
            };
        }

        const fallbackTargetPath = activeState.context?.workspaceFilePath ?? this.lastFollowablePythonTarget;
        if (!fallbackTargetPath) {
            return {
                filePath: activeState.filePath,
                reason: null,
                selectionMode: "follow_active",
                activeContext: activeState.context,
                targetContext: activeState.context,
                targetReference: null,
            };
        }
        const targetReference =
            activeState.context?.workspaceFilePath === fallbackTargetPath
                ? {
                    workspaceFilePath: activeState.context.workspaceFilePath,
                    absolutePath: activeState.context.absolutePath,
                }
                : this.resolveFileReference(fallbackTargetPath);
        return {
            filePath: fallbackTargetPath,
            reason: targetReference ? null : TARGET_MISSING_MESSAGE,
            selectionMode: "follow_active",
            activeContext: activeState.context,
            targetContext:
                activeState.context?.workspaceFilePath === fallbackTargetPath ? activeState.context : null,
            targetReference,
        };
    }

    private pruneSelectionsForTarget(targetFilePath: string | null): string | null {
        if (!targetFilePath) {
            return null;
        }

        const removedContext = this.selectedContextFiles.filter((item) => item === targetFilePath);
        const removedEditable = this.selectedEditableFiles.filter((item) => item === targetFilePath);
        if (removedContext.length === 0 && removedEditable.length === 0) {
            return null;
        }

        this.selectedContextFiles = this.selectedContextFiles.filter((item) => item !== targetFilePath);
        this.selectedEditableFiles = this.selectedEditableFiles.filter((item) => item !== targetFilePath);
        const parts: string[] = [];
        if (removedContext.length > 0) {
            parts.push(`removed ${removedContext.length} context file(s)`);
        }
        if (removedEditable.length > 0) {
            parts.push(`removed ${removedEditable.length} editable file(s)`);
        }
        return `Target changed; ${parts.join(" and ")} that matched the target.`;
    }

    private resolveRunSelections(targetState: CodeTargetState):
        | {
            ok: true;
            contextFiles: SelectedFileReference[];
            editableFiles: SelectedFileReference[];
        }
        | { ok: false; message: string } {
        const targetFilePath = targetState.targetReference?.workspaceFilePath;
        const contextFiles = this.selectedContextFiles;
        if (contextFiles.length > 3) {
            return { ok: false, message: TOO_MANY_CONTEXT_FILES_MESSAGE };
        }
        if (new Set(contextFiles).size !== contextFiles.length) {
            return { ok: false, message: DUPLICATE_CONTEXT_FILE_MESSAGE };
        }

        const resolvedContextFiles: SelectedFileReference[] = [];
        for (const workspaceFilePath of contextFiles) {
            if (workspaceFilePath === targetFilePath) {
                return { ok: false, message: CONTEXT_TARGET_CONFLICT_MESSAGE };
            }
            const reference = this.resolveFileReference(workspaceFilePath);
            if (!reference) {
                return {
                    ok: false,
                    message: `Context file not found in the current workspace: ${workspaceFilePath}`,
                };
            }
            resolvedContextFiles.push(reference);
        }

        const editableFiles = this.selectedEditableFiles;
        if (editableFiles.length > 1) {
            return { ok: false, message: TOO_MANY_EDITABLE_FILES_MESSAGE };
        }
        if (new Set(editableFiles).size !== editableFiles.length) {
            return { ok: false, message: DUPLICATE_EDITABLE_FILE_MESSAGE };
        }

        const contextPaths = new Set(contextFiles);
        const resolvedEditableFiles: SelectedFileReference[] = [];
        for (const workspaceFilePath of editableFiles) {
            if (workspaceFilePath === targetFilePath) {
                return { ok: false, message: EDITABLE_TARGET_CONFLICT_MESSAGE };
            }
            if (contextPaths.has(workspaceFilePath)) {
                return { ok: false, message: EDITABLE_CONTEXT_CONFLICT_MESSAGE };
            }
            const reference = this.resolveFileReference(workspaceFilePath);
            if (!reference) {
                return {
                    ok: false,
                    message: `Editable file not found in the current workspace: ${workspaceFilePath}`,
                };
            }
            resolvedEditableFiles.push(reference);
        }

        return {
            ok: true,
            contextFiles: resolvedContextFiles,
            editableFiles: resolvedEditableFiles,
        };
    }

    private async buildTaskContext(targetState: CodeTargetState): Promise<TaskContext | null> {
        const targetReference = targetState.targetReference;
        if (!targetReference) {
            return {
                taskId: null,
                absolutePath: null,
                workspaceFilePath: null,
                documentVersion: null,
                lineNumber: null,
                originalContent: null,
            };
        }
        const document = await this.openWorkspaceDocument(
            targetReference,
            targetState.activeContext,
        );
        if (!document) {
            return null;
        }
        const activeContext = targetState.activeContext;
        const lineNumber =
            activeContext?.workspaceFilePath === targetReference.workspaceFilePath
                ? activeContext.lineNumber
                : null;
        return {
            taskId: null,
            absolutePath: targetReference.absolutePath,
            workspaceFilePath: targetReference.workspaceFilePath,
            documentVersion: document.version,
            lineNumber,
            originalContent: document.getText(),
        };
    }

    private async buildEditablePayload(
        editableFiles: SelectedFileReference[],
    ): Promise<Array<{ file_path: string; document_version: number }> | null> {
        const items: Array<{ file_path: string; document_version: number }> = [];
        for (const editable of editableFiles) {
            const document = await this.openWorkspaceDocument(editable, null);
            if (!document) {
                return null;
            }
            items.push({
                file_path: editable.workspaceFilePath,
                document_version: document.version,
            });
        }
        return items;
    }

    private async precheckVerifiedFiles(
        verifiedFiles: NonNullable<CodingTaskDraftResult["verified_files"]>,
    ): Promise<string | null> {
        for (const item of verifiedFiles) {
            const reference = this.resolveFileReference(item.file_path);
            if (!reference) {
                return `${item.file_path} is no longer available in the current workspace.`;
            }
            const document = await this.openWorkspaceDocument(reference, null);
            if (!document) {
                return `${item.file_path} is no longer available in the current workspace.`;
            }
            const currentContent = document.getText();
            if (
                item.original_document_version != null &&
                document.version !== item.original_document_version
            ) {
                return `${item.file_path} changed locally. The current draft is no longer safe to apply; run the task again.`;
            }
            if (hashContent(currentContent) !== item.original_content_hash) {
                return `${item.file_path} changed locally. The current draft is no longer safe to apply; run the task again.`;
            }
        }
        return null;
    }

    private shouldRefreshExplainAfterApply(): boolean {
        if (!this.currentTaskContext || this.currentTaskContext.lineNumber == null) {
            return false;
        }
        const activeEditor = this.runtime.getActiveEditor();
        if (!activeEditor || activeEditor.document.uri.scheme !== "file") {
            return false;
        }
        return activeEditor.document.uri.fsPath === this.currentTaskContext.absolutePath;
    }

    private sanitizeSelectionPaths(
        workspaceFilePaths: string[],
        options: {
            kind: "context" | "editable";
            max: number;
            targetFilePath: string | null;
            blockedPaths: string[];
        },
    ): { accepted: string[]; skipped: string[] } {
        const accepted: string[] = [];
        const skipped: string[] = [];
        const seen = new Set<string>();

        for (const workspaceFilePath of workspaceFilePaths) {
            if (!workspaceFilePath || seen.has(workspaceFilePath)) {
                continue;
            }
            seen.add(workspaceFilePath);
            if (options.targetFilePath && workspaceFilePath === options.targetFilePath) {
                skipped.push(`${workspaceFilePath} (conflicts with target)`);
                continue;
            }
            if (options.blockedPaths.includes(workspaceFilePath)) {
                skipped.push(
                    `${workspaceFilePath} (${options.kind === "context" ? "conflicts with editable files" : "conflicts with context files"})`,
                );
                continue;
            }
            if (!this.resolveAbsoluteWorkspacePath(workspaceFilePath)) {
                skipped.push(`${workspaceFilePath} (missing from workspace)`);
                continue;
            }
            if (accepted.length >= options.max) {
                skipped.push(`${workspaceFilePath} (limit exceeded)`);
                continue;
            }
            accepted.push(workspaceFilePath);
        }

        return { accepted, skipped };
    }

    private buildHistoryNotice(baseMessage: string, skipped: string[]): string {
        if (skipped.length === 0) {
            return baseMessage;
        }
        return `${baseMessage} Skipped: ${skipped.join(", ")}.`;
    }

    private async refreshWorkspacePythonFiles(): Promise<void> {
        this.workspacePythonFiles = await this.listWorkspacePythonFiles();
    }

    private resetAllPickerDrafts(): void {
        this.openCodePicker = null;
        this.pickerSearch.target = "";
        this.pickerSearch.context = "";
        this.pickerSearch.editable = "";
        this.draftTargetFilePath = null;
        this.draftContextFiles = [];
        this.draftEditableFiles = [];
    }

    private getCurrentExplainEntityTargetState(): ExplainEntityTargetState {
        if (this.lastExplainState.type !== "state:update") {
            return {
                available: false,
                entityId: null,
                entityName: null,
                filePath: null,
                canUseAsTarget: false,
            };
        }
        const filePath = this.lastExplainState.data.filePath;
        const canUseAsTarget =
            filePath.toLowerCase().endsWith(".py") &&
            this.resolveAbsoluteWorkspacePath(filePath) !== null;
        return {
            available: true,
            entityId: this.lastExplainState.data.entityId,
            entityName: this.lastExplainState.data.entityName,
            filePath,
            canUseAsTarget,
        };
    }

    private getEligiblePickerCandidateCount(
        kind: CodePickerKind,
        targetState: CodeTargetState,
    ): number {
        return this.buildPickerCandidates(kind, targetState).length;
    }

    private buildTargetPickerViewModel(targetState: CodeTargetState): CodePickerViewModel {
        const selectedTargetFilePath =
            this.openCodePicker === "target"
                ? this.draftTargetFilePath
                : targetState.filePath;
        return {
            open: this.openCodePicker === "target",
            search: this.pickerSearch.target,
            candidates: this.buildPickerCandidates("target", targetState).map((item) => {
                return {
                    path: item.workspaceFilePath,
                    selected: item.workspaceFilePath === selectedTargetFilePath,
                };
            }),
        };
    }

    private buildContextPickerViewModel(targetState: CodeTargetState): CodePickerViewModel {
        const selectedContextFiles =
            this.openCodePicker === "context" ? this.draftContextFiles : this.selectedContextFiles;
        return {
            open: this.openCodePicker === "context",
            search: this.pickerSearch.context,
            candidates: this.buildPickerCandidates("context", targetState).map((item) => {
                return {
                    path: item.workspaceFilePath,
                    selected: selectedContextFiles.includes(item.workspaceFilePath),
                };
            }),
        };
    }

    private buildEditablePickerViewModel(targetState: CodeTargetState): CodePickerViewModel {
        const selectedEditableFiles =
            this.openCodePicker === "editable" ? this.draftEditableFiles : this.selectedEditableFiles;
        return {
            open: this.openCodePicker === "editable",
            search: this.pickerSearch.editable,
            candidates: this.buildPickerCandidates("editable", targetState).map((item) => {
                return {
                    path: item.workspaceFilePath,
                    selected: selectedEditableFiles.includes(item.workspaceFilePath),
                };
            }),
        };
    }

    private buildExplainEntityViewModel(): CodeExplainEntityViewModel {
        const explainEntity = this.getCurrentExplainEntityTargetState();
        return {
            available: explainEntity.available,
            entityId: explainEntity.entityId,
            entityName: explainEntity.entityName,
            filePath: explainEntity.filePath,
            canUseAsTarget: explainEntity.canUseAsTarget,
        };
    }

    private buildPickerCandidates(
        kind: CodePickerKind,
        targetState: CodeTargetState,
    ): WorkspaceFileCandidate[] {
        const normalizedSearch = this.pickerSearch[kind].trim().toLowerCase();
        const targetFilePath = targetState.targetReference?.workspaceFilePath ?? targetState.filePath;

        let candidates = [...this.workspacePythonFiles];
        if (kind === "context") {
            const excluded = new Set(this.selectedEditableFiles);
            if (targetFilePath) {
                excluded.add(targetFilePath);
            }
            candidates = candidates.filter((item) => !excluded.has(item.workspaceFilePath));
        } else if (kind === "editable") {
            const excluded = new Set(this.selectedContextFiles);
            if (targetFilePath) {
                excluded.add(targetFilePath);
            }
            candidates = candidates.filter((item) => !excluded.has(item.workspaceFilePath));
        }

        if (!normalizedSearch) {
            return candidates;
        }
        return candidates.filter((item) => {
            return item.workspaceFilePath.toLowerCase().includes(normalizedSearch);
        });
    }

    private async applyExplicitTargetSelection(workspaceFilePath: string): Promise<void> {
        this.resetAllPickerDrafts();
        this.targetSelectionMode = "explicit";
        this.selectedTargetFilePath = workspaceFilePath;
        const notice = this.pruneSelectionsForTarget(workspaceFilePath);
        await this.runtime.openWorkspaceFile?.(workspaceFilePath);
        this.codeHistoryNotice = notice;
        await this.postCodeState();
    }

    private async listWorkspacePythonFiles(): Promise<WorkspaceFileCandidate[]> {
        return this.runtime.listWorkspacePythonFiles?.() ?? [];
    }

    private resolveFileReference(workspaceFilePath: string): SelectedFileReference | null {
        const absolutePath = this.resolveAbsoluteWorkspacePath(workspaceFilePath);
        if (!absolutePath) {
            return null;
        }
        return {
            workspaceFilePath,
            absolutePath,
        };
    }

    private async openWorkspaceDocument(
        reference: SelectedFileReference,
        activeContext: EditorContext | null,
    ): Promise<vscode.TextDocument | null> {
        if (activeContext && activeContext.workspaceFilePath === reference.workspaceFilePath) {
            return activeContext.editor.document;
        }
        const openDocument =
            this.runtime.getOpenDocumentByAbsolutePath?.(reference.absolutePath) ?? null;
        if (openDocument) {
            return openDocument;
        }
        return this.runtime.openWorkspaceDocument?.(reference.workspaceFilePath) ?? null;
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

    private getCodeEffectiveProfile(): EffectiveProfileViewModel {
        return resolveCodeEffectiveProfile(
            this.profileStateStore.getProfiles(),
            this.overrideCodeProfileId ?? this.getCodeProfilePreferenceId(),
        );
    }

    private getExplainEffectiveProfile(): EffectiveProfileViewModel {
        const codeProfile = this.getCodeEffectiveProfile();
        return resolveExplainEffectiveProfile(
            this.profileStateStore.getProfiles(),
            codeProfile,
            this.getExplainProfilePreferenceId(),
        );
    }

    private getCapabilitySnapshot(): BackendCodingCapabilitiesResponse | null {
        return this.profileStateStore.getCapabilities();
    }

    private beginConversationRun(
        prompt: string,
        targetState: CodeTargetState,
    ): void {
        const runId = this.createConversationRunId();
        const timestamp = new Date().toISOString();
        this.currentConversationRunId = runId;
        this.currentThinkingCardId = null;
        this.currentLatestEditCardId = null;

        this.pushCodeTaskCard({
            id: this.createTaskCardId(runId, "run_marker"),
            runId,
            kind: "run_marker",
            targetFilePath: targetState.filePath,
            timestamp,
            launchMode: this.currentTaskLaunchMode,
            sourceTaskId: this.currentTaskSourceTaskId,
        });
        this.pushCodeTaskCard({
            id: this.createTaskCardId(runId, "user_message"),
            runId,
            kind: "user_message",
            prompt,
            targetFilePath: targetState.filePath,
            contextFiles: [...this.selectedContextFiles],
            editableFiles: [...this.selectedEditableFiles],
        });
    }

    private createConversationRunId(): string {
        this.nextTaskCardOrdinal += 1;
        return `run_${Date.now()}_${this.nextTaskCardOrdinal}`;
    }

    private createTaskCardId(runId: string, kind: CodeTaskCardViewModel["kind"]): string {
        this.nextTaskCardOrdinal += 1;
        return `${runId}_${kind}_${this.nextTaskCardOrdinal}`;
    }

    private pushCodeTaskCard(card: CodeTaskCardViewModel): void {
        this.codeTaskCards = [...this.codeTaskCards, card];
    }

    private updateCodeTaskCard(
        cardId: string,
        updater: (card: CodeTaskCardViewModel) => CodeTaskCardViewModel,
    ): void {
        this.codeTaskCards = this.codeTaskCards.map((card) => {
            if (card.id !== cardId) {
                return card;
            }
            return updater(card);
        });
    }

    private clearCodeConversation(): void {
        this.codeTaskCards = [];
        this.currentConversationRunId = null;
        this.currentThinkingCardId = null;
        this.currentLatestEditCardId = null;
    }

    private appendThinkingDelta(text: string): void {
        if (!text || !this.currentConversationRunId) {
            return;
        }
        if (!this.currentThinkingCardId) {
            this.currentThinkingCardId = this.createTaskCardId(this.currentConversationRunId, "thinking");
            this.pushCodeTaskCard({
                id: this.currentThinkingCardId,
                runId: this.currentConversationRunId,
                kind: "thinking",
                text,
                streaming: true,
            });
            return;
        }
        this.updateCodeTaskCard(this.currentThinkingCardId, (card) => {
            if (card.kind !== "thinking") {
                return card;
            }
            return {
                ...card,
                text: `${card.text}${text}`,
                streaming: true,
            };
        });
    }

    private finalizeThinkingCard(): void {
        if (!this.currentThinkingCardId) {
            return;
        }
        const thinkingCardId = this.currentThinkingCardId;
        this.currentThinkingCardId = null;
        this.updateCodeTaskCard(thinkingCardId, (card) => {
            if (card.kind !== "thinking") {
                return card;
            }
            return {
                ...card,
                streaming: false,
            };
        });
    }

    private appendStepCard(step: BackendTaskStepEvent): void {
        if (!this.currentConversationRunId) {
            return;
        }
        if (step.step_kind === "view") {
            const cardId = `${this.currentConversationRunId}_tool_${step.step_id}`;
            const existing = this.codeTaskCards.find((item) => item.id === cardId);
            const card = {
                id: cardId,
                runId: this.currentConversationRunId,
                kind: "tool_call" as const,
                stepId: step.step_id,
                toolName: step.tool_name ?? "view_file",
                filePath: step.file_path,
                status: step.status,
                summary: step.output_summary ?? step.input_summary ?? step.intent,
            };
            if (existing) {
                this.updateCodeTaskCard(cardId, () => card);
            } else {
                this.pushCodeTaskCard(card);
            }
            return;
        }
        if (step.step_kind === "edit") {
            const cardId = `${this.currentConversationRunId}_edit_${step.step_id}`;
            this.currentLatestEditCardId = cardId;
            const existing = this.codeTaskCards.find((item) => item.id === cardId);
            const card = {
                id: cardId,
                runId: this.currentConversationRunId,
                kind: "file_change" as const,
                stepId: step.step_id,
                filePath: step.file_path,
                status: step.status,
                summary: step.output_summary ?? step.reasoning_summary ?? step.intent,
                draftFiles: [],
                diffAvailable: false,
            };
            if (existing) {
                this.updateCodeTaskCard(cardId, () => card);
            } else {
                this.pushCodeTaskCard(card);
            }
            return;
        }
        const cardId = `${this.currentConversationRunId}_verify_${step.step_id}`;
        const existing = this.codeTaskCards.find((item) => item.id === cardId);
        const card = {
            id: cardId,
            runId: this.currentConversationRunId,
            kind: "verify" as const,
            stepId: step.step_id,
            filePath: step.file_path,
            status: step.status,
            summary: step.output_summary ?? step.reasoning_summary ?? step.intent,
        };
        if (existing) {
            this.updateCodeTaskCard(cardId, () => card);
        } else {
            this.pushCodeTaskCard(card);
        }
    }

    private async attachDraftFilesToConversation(): Promise<CodeTaskCardDraftFileViewModel[]> {
        const targetState = this.getCodeTargetState();
        const draftFiles = await this.buildCurrentDraftFiles(targetState.activeContext);
        const taskDraftFiles = draftFiles.map((item) => {
            return {
                filePath: item.filePath,
                content: item.content,
                baseContent: item.baseContent,
                baseSource: item.baseSource,
            };
        });
        if (!this.currentLatestEditCardId) {
            return taskDraftFiles;
        }
        this.updateCodeTaskCard(this.currentLatestEditCardId, (card) => {
            if (card.kind !== "file_change") {
                return card;
            }
            const matchingDraftFiles = taskDraftFiles.filter((item) => item.filePath === card.filePath);
            const nextDraftFiles = matchingDraftFiles.length > 0 ? matchingDraftFiles : taskDraftFiles;
            return {
                ...card,
                draftFiles: nextDraftFiles,
                diffAvailable: nextDraftFiles.some((item) => item.baseSource === "workspace_live"),
            };
        });
        return taskDraftFiles;
    }

    private async pushDraftReadyCard(taskId: string): Promise<void> {
        if (!this.currentConversationRunId) {
            return;
        }
        const draftFiles = await this.attachDraftFilesToConversation();
        const cardId = `${this.currentConversationRunId}_draft_ready`;
        const card = {
            id: cardId,
            runId: this.currentConversationRunId,
            kind: "draft_ready" as const,
            taskId,
            fileCount: draftFiles.length,
            files: draftFiles.map((item) => {
                return {
                    filePath: item.filePath,
                    diffAvailable: item.baseSource === "workspace_live",
                };
            }),
            draftFiles,
            applied: false,
        };
        const existing = this.codeTaskCards.find((item) => item.id === cardId);
        if (existing) {
            this.updateCodeTaskCard(cardId, () => card);
        } else {
            this.pushCodeTaskCard(card);
        }
    }

    private markLatestDraftReadyApplied(): void {
        if (!this.currentConversationRunId) {
            return;
        }
        const cardId = `${this.currentConversationRunId}_draft_ready`;
        this.updateCodeTaskCard(cardId, (card) => {
            if (card.kind !== "draft_ready") {
                return card;
            }
            return {
                ...card,
                applied: true,
            };
        });
    }

    private pushErrorCard(message: string, rawMessage?: string | null): void {
        if (!message || !this.currentConversationRunId) {
            return;
        }
        this.finalizeThinkingCard();
        this.pushCodeTaskCard({
            id: this.createTaskCardId(this.currentConversationRunId, "error"),
            runId: this.currentConversationRunId,
            kind: "error",
            message,
            rawMessage: rawMessage ?? message,
        });
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
        const targetState = this.getCodeTargetState();
        const launchMode =
            this.pendingReplaySourceTaskId !== null ? "replay" : this.currentTaskLaunchMode;
        const sourceTaskId = this.pendingReplaySourceTaskId ?? this.currentTaskSourceTaskId;
        const codeProfile = this.getCodeEffectiveProfile();
        const explainProfile = this.getExplainEffectiveProfile();
        const capabilitySummary = buildCapabilitySummary(this.getCapabilitySnapshot());
        const draftFiles = await this.buildCurrentDraftFiles(targetState.activeContext);
        const conversation = buildConversationViewModel({
            taskCards: this.codeTaskCards,
            status: this.codeStatus,
            recentTasks: this.recentTaskSummaries,
            composerHintTarget: targetState.filePath,
            composerContextFiles: this.selectedContextFiles,
        });
        const data: CodeViewModel = {
            filePath: targetState.filePath,
            targetSelectionMode: targetState.selectionMode,
            contextFiles: [...this.selectedContextFiles],
            editableFiles: [...this.selectedEditableFiles],
            targetPicker: this.buildTargetPickerViewModel(targetState),
            contextPicker: this.buildContextPickerViewModel(targetState),
            editablePicker: this.buildEditablePickerViewModel(targetState),
            explainEntity: this.buildExplainEntityViewModel(),
            status: this.codeStatus,
            launchMode,
            sourceTaskId,
            transcriptText: this.codeTranscriptText,
            modelOutputText: this.codeModelOutputText,
            draftText: this.codeDraftText,
            draftFiles,
            conversation,
            message:
                this.codeMessage ??
                codeProfile.reason ??
                targetState.reason ??
                defaultCodeMessage(this.codeStatus),
            canRun:
                codeProfile.available &&
                this.codeStatus !== "running" &&
                this.codeStatus !== "applying",
            canCancel: this.codeStatus === "running",
            canApply: this.codeStatus === "ready_to_apply" && this.currentTaskResult !== null,
            codeProfile,
            explainProfile,
            capabilitySummary,
            historyLoading: this.codeHistoryLoading,
            historyError: this.codeHistoryError,
            historyNotice: this.codeHistoryNotice,
            historyPage: this.buildHistoryPageViewModel(),
            historyFilters: this.buildHistoryFiltersViewModel(),
            historyItems: this.codeHistoryItems,
            historyDetail: this.codeHistoryDetail,
        };
        await this.postMessage({
            type: "code:update",
            data,
        });
    }

    private buildHistoryPageViewModel(): CodeHistoryPageViewModel {
        return {
            total: this.historyQueryState.total,
            filteredCount: this.codeHistoryItems.length,
            limit: this.historyQueryState.limit,
            offset: this.historyQueryState.offset,
            hasMore: this.historyQueryState.hasMore,
        };
    }

    private buildHistoryFiltersViewModel(): CodeHistoryFiltersViewModel {
        return {
            status: this.historyQueryState.status,
            targetFilePath: this.historyQueryState.targetFilePath,
            targetQuery: this.historyQueryState.targetQuery,
            targetSuggestions: [...this.historyTargetSuggestions],
            targetSuggestionsLoading: this.historyTargetSuggestionsLoading,
        };
    }

    private resolveSelectedHistoryTaskId(
        items: Array<{ task_id: string }>,
        preferredTaskId?: string | null,
    ): string | null {
        if (preferredTaskId && items.some((item) => item.task_id === preferredTaskId)) {
            return preferredTaskId;
        }
        if (
            this.selectedHistoryTaskId &&
            items.some((item) => item.task_id === this.selectedHistoryTaskId)
        ) {
            return this.selectedHistoryTaskId;
        }
        return items[0]?.task_id ?? null;
    }

    private async postMessage(message: SidebarMessageToWebview): Promise<void> {
        if (!this.view) {
            return;
        }
        await this.view.webview.postMessage(message);
    }

    private renderHtml(webview: vscode.Webview): string {
        if (this.getReactAssetUris && !this.shouldUseLegacyWebview()) {
            return this.renderReactHtml(webview);
        }
        return this.renderLegacyHtml(webview.cspSource);
    }

    private renderLegacyHtml(cspSource: string): string {
        const nonce = randomBytes(16).toString("base64");
        return this.template
            .replaceAll("__NONCE__", nonce)
            .replaceAll("__CSP_SOURCE__", cspSource);
    }

    private renderReactHtml(webview: vscode.Webview): string {
        if (!this.getReactAssetUris) {
            return this.renderLegacyHtml(webview.cspSource);
        }
        const nonce = randomBytes(16).toString("base64");
        const { scriptUri, styleUri } = this.getReactAssetUris(webview);
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta
        http-equiv="Content-Security-Policy"
        content="default-src 'none'; img-src ${webview.cspSource} https: data:; style-src ${webview.cspSource}; script-src 'nonce-${nonce}'; font-src ${webview.cspSource};"
    />
    <title>TailEvents</title>
    <link rel="stylesheet" href="${styleUri}" />
</head>
<body>
    <div id="root"></div>
    <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
    }

    private async buildCurrentDraftFiles(
        activeContext: EditorContext | null,
    ): Promise<DraftFileViewModel[]> {
        const verifiedFiles = this.currentTaskResult?.verified_files ?? [];
        if (verifiedFiles.length === 0) {
            return [];
        }
        return buildActiveDraftFileViewModels(verifiedFiles, async (workspaceFilePath) => {
            if (!isSafeWorkspaceFilePath(workspaceFilePath)) {
                return null;
            }
            const reference = this.resolveFileReference(workspaceFilePath);
            if (!reference) {
                return null;
            }
            const document = await this.openWorkspaceDocument(reference, activeContext);
            return document?.getText() ?? null;
        });
    }

    private async openWorkspaceFile(workspaceFilePath: string): Promise<void> {
        if (!isSafeWorkspaceFilePath(workspaceFilePath)) {
            return;
        }
        const reference = this.resolveFileReference(workspaceFilePath);
        if (!reference) {
            return;
        }
        await this.runtime.openWorkspaceFile?.(reference.workspaceFilePath);
    }

    private async openDiffView(workspaceFilePath: string): Promise<void> {
        if (!isSafeWorkspaceFilePath(workspaceFilePath)) {
            return;
        }
        const reference = this.resolveFileReference(workspaceFilePath);
        if (!reference) {
            return;
        }
        const draftContent = this.findLatestDraftContentForPath(workspaceFilePath);
        if (draftContent === null) {
            return;
        }
        await this.runtime.openDiffView?.(reference.workspaceFilePath, draftContent);
    }

    private findLatestDraftContentForPath(workspaceFilePath: string): string | null {
        const currentDraft = this.currentTaskResult?.verified_files?.find((item) => {
            return item.file_path === workspaceFilePath;
        });
        if (currentDraft) {
            return currentDraft.content;
        }
        for (let index = this.codeTaskCards.length - 1; index >= 0; index -= 1) {
            const card = this.codeTaskCards[index];
            if (card.kind !== "file_change" && card.kind !== "draft_ready") {
                continue;
            }
            const matchingDraft = card.draftFiles.find((item) => item.filePath === workspaceFilePath);
            if (matchingDraft) {
                return matchingDraft.content;
            }
        }
        return null;
    }

    private getCurrentLabel(): string | undefined {
        if (this.lastExplainState.type !== "state:update") {
            return undefined;
        }
        return this.lastExplainState.data.entityName;
    }
}

function buildInitialViewModel(
    payload: BackendExplanationStreamInit,
    profile: EffectiveProfileViewModel,
): SidebarViewModel {
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
        historySource: payload.history_source,
        disclaimer: getDisclaimer(payload.history_source),
        detailedExplanation: null,
        streamError: null,
        timeline: [],
        historyAvailable: false,
        historyLoading: true,
        callers: [],
        callees: [],
        relatedEntities: [],
        globalImpactPaths: [],
        globalImpactSummary: null,
        globalImpactEmptyText: "No global paths yet.",
        externalDocs: [],
        externalDocsPlaceholder: "暂未接入",
        profile: {
            ...profile,
            resolvedProfileId: payload.resolved_profile_id ?? profile.resolvedProfileId,
        },
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
    viewModel.historySource = explanation.history_source;
    viewModel.disclaimer = getDisclaimer(explanation.history_source);
    viewModel.detailedExplanation =
        explanation.detailed_explanation ?? viewModel.detailedExplanation ?? null;
    viewModel.callers = buildContextImpactItems(
        explanation.relation_context?.local?.callers ?? [],
        "incoming",
        "caller",
    );
    viewModel.callees = buildContextImpactItems(
        explanation.relation_context?.local?.callees ?? [],
        "outgoing",
        "callee",
    );
    viewModel.relatedEntities = buildRelatedEntities(explanation);
    viewModel.globalImpactPaths = buildGlobalImpactPaths(explanation);
    viewModel.globalImpactSummary = buildGlobalImpactSummary(explanation);
    viewModel.globalImpactEmptyText = buildGlobalImpactEmptyText(explanation);
    viewModel.externalDocs = buildExternalDocs(explanation);
    viewModel.externalDocsPlaceholder =
        viewModel.externalDocs.length > 0 ? "" : "暂未接入";
    viewModel.streamError = null;
    if (viewModel.profile) {
        viewModel.profile = {
            ...viewModel.profile,
            resolvedProfileId:
                explanation.resolved_profile_id ?? viewModel.profile.resolvedProfileId,
        };
    }
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

function buildGlobalImpactPaths(explanation: BackendEntityExplanation) {
    return (explanation.relation_context?.global?.paths ?? []).map((item) => {
        const qualifiedPath = buildQualifiedImpactPath(item);
        const evidenceLabel = item.evidence_level === "strong" ? "strong evidence" : "limited evidence";
        const terminalReason = formatImpactTerminalReason(item.terminal_reason, item.direction);
        const truncationReason = formatImpactTruncationReason(item.truncation_reason);
        const suffixParts = [terminalReason, truncationReason].filter((part) => part.length > 0);
        return {
            direction: item.direction,
            terminalEntityId: item.terminal_entity_id,
            terminalLabel:
                item.terminal_qualified_name.split(".").at(-1) ?? item.terminal_qualified_name,
            qualifiedPath,
            costLabel: `${evidenceLabel} • cost ${item.cost} • hops ${item.hop_count}${suffixParts.length ? ` • ${suffixParts.join(" • ")}` : ""}`,
        };
    });
}

function buildGlobalImpactSummary(explanation: BackendEntityExplanation): string | null {
    const subgraph = explanation.relation_context?.global?.subgraph;
    if (!subgraph) {
        return null;
    }
    const suffix = subgraph.truncated ? " • truncated" : "";
    return `Depth ${subgraph.depth} • ${subgraph.node_count} nodes • ${subgraph.edge_count} edges${suffix}`;
}

function buildGlobalImpactEmptyText(explanation: BackendEntityExplanation): string {
    const globalContext = explanation.relation_context?.global;
    const hasSubgraph = globalContext?.subgraph !== null && globalContext?.subgraph !== undefined;
    const hasPaths = (globalContext?.paths ?? []).length > 0;
    if (hasSubgraph && !hasPaths) {
        return "No terminal paths yet. Local relations and the subgraph summary are still available.";
    }
    return "No global paths yet.";
}

function buildQualifiedImpactPath(item: BackendGlobalImpactPath) {
    const steps = item.steps ?? [];
    const relations = item.step_relations ?? [];
    if (steps.length <= 1) {
        return steps[0]?.qualified_name ?? "";
    }
    const parts: string[] = [steps[0].qualified_name];
    for (let index = 1; index < steps.length; index += 1) {
        const relationLabel = relations[index - 1] ?? "linked";
        parts.push(`-${relationLabel}->`);
        parts.push(steps[index].qualified_name);
    }
    return parts.join(" ");
}

function formatImpactTerminalReason(reason: string | undefined, direction: "upstream" | "downstream"): string {
    switch (reason) {
        case "module_root":
            return direction === "upstream" ? "module root" : "module boundary";
        case "inheritance_root":
            return "inheritance root";
        case "inheritance_leaf":
            return "inheritance leaf";
        case "call_boundary":
            return "call boundary";
        case "frontier":
            return "frontier";
        default:
            return "";
    }
}

function formatImpactTruncationReason(reason: string | null | undefined): string {
    switch (reason) {
        case "frontier":
            return "frontier fallback";
        case "hop_limit":
            return "hop-limited";
        case "expansion_limit":
            return "search-limited";
        default:
            return "";
    }
}

function buildExternalDocs(explanation: BackendEntityExplanation) {
    return (explanation.external_doc_snippets ?? []).map((item) => {
        const title = `${item.source.package}.${item.source.symbol}`;
        const sourceLabel =
            item.source.kind === "workspace_doc"
                ? item.source.file_path ?? "workspace doc"
                : "pydoc";
        return {
            title,
            sourceLabel,
            excerpt: item.chunk.content,
        };
    });
}

function buildContextImpactItems(
    items: Array<{
        entity_id: string;
        qualified_name: string;
        relation: string;
    }>,
    direction: string,
    relationLabel: string,
): RelatedEntityViewModel[] {
    return items.map((item) => {
        const label = item.qualified_name.split(".").at(-1) ?? item.qualified_name;
        return {
            entityId: String(item.entity_id),
            label,
            relationLabel,
            qualifiedName: item.qualified_name,
            direction,
        };
    });
}

function getDisclaimer(historySource: string | null | undefined): string | null {
    if (historySource === "baseline_only") {
        return BASELINE_ONLY_DISCLAIMER;
    }
    if (historySource === "mixed") {
        return MIXED_DISCLAIMER;
    }
    return null;
}

function buildConversationViewModel(options: {
    taskCards: CodeTaskCardViewModel[];
    status: CodeTaskStatus;
    recentTasks: RecentTaskSummaryViewModel[];
    composerHintTarget: string | null;
    composerContextFiles: string[];
}): CodeConversationViewModel {
    const runs = new Map<string, CodeConversationRunViewModel>();
    const runTimestamps = new Map<string, string>();
    let latestRunId: string | null = null;

    for (const card of options.taskCards) {
        if (card.kind === "run_marker") {
            runs.set(card.runId, {
                runId: card.runId,
                sourceTaskId: card.sourceTaskId,
                targetFilePath: card.targetFilePath,
                launchMode: card.launchMode,
                status: "idle",
                messages: [],
            });
            runTimestamps.set(card.runId, card.timestamp);
            latestRunId = card.runId;
            continue;
        }

        const run = runs.get(card.runId);
        if (!run) {
            continue;
        }
        latestRunId = card.runId;
        const runTimestamp = runTimestamps.get(card.runId) ?? new Date().toISOString();

        if (card.kind === "user_message") {
            run.messages.push({
                id: card.id,
                runId: card.runId,
                kind: "user_turn",
                text: card.prompt,
                timestamp: runTimestamp,
            });
            continue;
        }

        if (card.kind === "thinking") {
            const workingMessage = ensureAssistantWorkingMessage(run, runTimestamp);
            workingMessage.details = appendReasoningDetails(workingMessage.details, card.text);
            continue;
        }

        if (card.kind === "tool_call" || card.kind === "file_change" || card.kind === "verify") {
            const workingMessage = ensureAssistantWorkingMessage(run, runTimestamp);
            workingMessage.details = appendToolTraceDetails(workingMessage.details, card);
            continue;
        }

        if (card.kind === "draft_ready") {
            const result = buildAssistantResultPayload(card);
            run.result = result;
            run.status = card.applied ? "applied" : "ready_to_apply";
            run.messages.push({
                id: card.id,
                runId: card.runId,
                kind: "assistant_result",
                text: result.summary,
                timestamp: runTimestamp,
                details: {
                    toolTrace: [],
                    reasoningSummary: null,
                    fileChanges: buildAssistantFileChanges(card.draftFiles, "Verified draft prepared."),
                    verifySummary: [],
                    rawTranscriptSnippet: null,
                },
                actions: buildAssistantResultActions(result),
            });
            continue;
        }

        if (card.kind === "error") {
            run.result = {
                summary: card.message,
                fileCount: 0,
                files: [],
                readyToApply: false,
                applied: false,
                errorMessage: card.message,
            };
            run.status = "failed";
            const errorText = summarizeAssistantError(card.message);
            run.messages.push({
                id: card.id,
                runId: card.runId,
                kind: "assistant_error",
                text: errorText,
                timestamp: runTimestamp,
                details: appendErrorDetails(
                    cloneLatestWorkingDetails(run.messages),
                    card.rawMessage ?? card.message,
                ),
                actions: [],
            });
        }
    }

    const orderedRuns = [...runs.values()];
    if (latestRunId) {
        const latestRun = orderedRuns.find((item) => item.runId === latestRunId);
        if (latestRun && latestRun.result === undefined) {
            latestRun.status = options.status;
        }
    }

    return {
        runs: orderedRuns,
        composerHintTarget: options.composerHintTarget,
        composerContextFiles: [...options.composerContextFiles],
        recentTasks: [...options.recentTasks],
    };
}

function ensureAssistantWorkingMessage(
    run: CodeConversationRunViewModel,
    timestamp: string,
): CodeConversationMessageViewModel {
    const existing = run.messages.find((message) => message.kind === "assistant_working");
    if (existing) {
        return existing;
    }
    const message: CodeConversationMessageViewModel = {
        id: `${run.runId}_assistant_working`,
        runId: run.runId,
        kind: "assistant_working",
        text: "I'm working through this change.",
        timestamp,
        details: createEmptyAssistantTurnDetails(),
        actions: [],
    };
    run.messages.push(message);
    return message;
}

function createEmptyAssistantTurnDetails(): AssistantTurnDetails {
    return {
        toolTrace: [],
        reasoningSummary: null,
        fileChanges: [],
        verifySummary: [],
        rawTranscriptSnippet: null,
    };
}

function appendReasoningDetails(
    details: AssistantTurnDetails | null | undefined,
    text: string,
): AssistantTurnDetails {
    const base = details ?? createEmptyAssistantTurnDetails();
    const nextReasoning = `${base.reasoningSummary ?? ""}${text}`.trim();
    const nextTranscript = `${base.rawTranscriptSnippet ?? ""}${text}`.trim();
    return {
        ...base,
        reasoningSummary: nextReasoning.length > 0 ? nextReasoning : null,
        rawTranscriptSnippet: nextTranscript.length > 0 ? nextTranscript : null,
    };
}

function appendToolTraceDetails(
    details: AssistantTurnDetails | null | undefined,
    card:
        | Extract<CodeTaskCardViewModel, { kind: "tool_call" }>
        | Extract<CodeTaskCardViewModel, { kind: "file_change" }>
        | Extract<CodeTaskCardViewModel, { kind: "verify" }>,
): AssistantTurnDetails {
    const base = details ?? createEmptyAssistantTurnDetails();
    const toolTrace = [...base.toolTrace];
    const traceItem = buildToolTraceItem(card);
    const existingIndex = toolTrace.findIndex((item) => item.stepId === traceItem.stepId);
    if (existingIndex >= 0) {
        toolTrace[existingIndex] = traceItem;
    } else {
        toolTrace.push(traceItem);
    }

    let fileChanges = base.fileChanges;
    if (card.kind === "file_change" && card.draftFiles.length > 0) {
        fileChanges = buildAssistantFileChanges(card.draftFiles, card.summary);
    }

    return {
        ...base,
        toolTrace,
        fileChanges,
        verifySummary:
            card.kind === "verify" ? [...base.verifySummary, card.summary] : base.verifySummary,
    };
}

function buildToolTraceItem(
    card:
        | Extract<CodeTaskCardViewModel, { kind: "tool_call" }>
        | Extract<CodeTaskCardViewModel, { kind: "file_change" }>
        | Extract<CodeTaskCardViewModel, { kind: "verify" }>,
): AssistantToolTraceItemViewModel {
    return {
        stepId: card.stepId,
        stepKind:
            card.kind === "tool_call"
                ? "view"
                : card.kind === "file_change"
                    ? "edit"
                    : "verify",
        status: card.status,
        filePath: card.filePath,
        toolName: card.kind === "tool_call" ? card.toolName : null,
        summary: card.summary,
    };
}

function buildAssistantFileChanges(
    draftFiles: CodeTaskCardDraftFileViewModel[],
    summary: string,
): AssistantFileChangeViewModel[] {
    return draftFiles.map((item) => {
        return {
            ...item,
            summary,
            diffAvailable: item.baseSource === "workspace_live",
        };
    });
}

function buildAssistantResultPayload(
    card: Extract<CodeTaskCardViewModel, { kind: "draft_ready" }>,
): AssistantResultPayload {
    const summary = card.applied
        ? `I completed the task and applied ${card.fileCount} file change${card.fileCount === 1 ? "" : "s"}.`
        : `I prepared a verified draft touching ${card.fileCount} file${card.fileCount === 1 ? "" : "s"}.`;
    return {
        summary,
        fileCount: card.fileCount,
        files: [...card.files],
        readyToApply: !card.applied,
        applied: card.applied,
    };
}

function buildAssistantResultActions(result: AssistantResultPayload): MessageActionViewModel[] {
    const actions: MessageActionViewModel[] = [];
    if (result.files.length === 1) {
        actions.push({
            type: "open_diff",
            label: "Open diff",
            path: result.files[0].filePath,
            enabled: result.files[0].diffAvailable,
        });
    }
    if (result.readyToApply) {
        actions.push({ type: "apply", label: "Apply", enabled: true });
        actions.push({ type: "dismiss_result", label: "Dismiss", enabled: true });
    }
    return actions;
}

function cloneLatestWorkingDetails(
    messages: CodeConversationMessageViewModel[],
): AssistantTurnDetails | null {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (message.kind !== "assistant_working" || !message.details) {
            continue;
        }
        return {
            toolTrace: [...message.details.toolTrace],
            reasoningSummary: message.details.reasoningSummary,
            fileChanges: [...message.details.fileChanges],
            verifySummary: [...message.details.verifySummary],
            rawTranscriptSnippet: message.details.rawTranscriptSnippet,
        };
    }
    return null;
}

function appendErrorDetails(
    details: AssistantTurnDetails | null,
    rawError: string,
): AssistantTurnDetails {
    const base = details ?? createEmptyAssistantTurnDetails();
    const nextTrace = [base.rawTranscriptSnippet, `error: ${rawError}`]
        .filter((item) => item && item.trim().length > 0)
        .join("\n\n");
    return {
        ...base,
        rawTranscriptSnippet: nextTrace || null,
    };
}

function mergeHistoryItems(
    existing: CodingTaskHistoryItemViewModel[],
    incoming: Array<{
        task_id: string;
        target_file_path: string;
        user_prompt: string;
        status: string;
        created_at: string;
        updated_at: string;
    }>,
): CodingTaskHistoryItemViewModel[] {
    const merged = new Map<string, CodingTaskHistoryItemViewModel>();

    for (const item of existing) {
        merged.set(item.taskId, { ...item, selected: false });
    }
    for (const item of incoming) {
        merged.set(item.task_id, {
            taskId: item.task_id,
            targetFilePath: item.target_file_path,
            userPrompt: item.user_prompt,
            status: item.status as CodingTaskHistoryItemViewModel["status"],
            createdAt: item.created_at,
            updatedAt: item.updated_at,
            selected: false,
        });
    }

    return [...merged.values()].sort((left, right) => {
        return Date.parse(right.updatedAt) - Date.parse(left.updatedAt);
    });
}

function buildRecentTaskSummaries(
    result: ApiResult<BackendCodingTaskHistoryItem[] | {
        items: BackendCodingTaskHistoryItem[];
        total: number;
        limit: number;
        offset: number;
        has_more: boolean;
    }>,
): RecentTaskSummaryViewModel[] {
    if (!result.ok) {
        return [];
    }
    const items = Array.isArray(result.data) ? result.data : result.data.items;
    return items.slice(0, 5).map((item) => {
        return {
            taskId: item.task_id,
            targetFilePath: item.target_file_path,
            userPrompt: item.user_prompt,
            status: item.status,
            updatedAt: item.updated_at,
        };
    });
}

function toHistoryDetailViewModel(
    detail: BackendCodingTaskHistoryDetail,
): CodingTaskHistoryDetailViewModel {
    return {
        taskId: detail.task_id,
        targetFilePath: detail.target_file_path,
        userPrompt: detail.user_prompt,
        contextFiles: detail.context_files,
        editableFiles: detail.editable_files ?? [],
        status: detail.status,
        createdAt: detail.created_at,
        updatedAt: detail.updated_at,
        transcriptText: detail.steps.map((step) => formatStepTranscript(step)).join("\n"),
        modelOutputText: detail.model_output_text ?? "",
        draftText:
            (detail.verified_files ?? []).length > 0
                ? (detail.verified_files ?? [])
                    .map((item) => `${item.file_path}\n${item.content}`)
                    .join("\n\n")
                : detail.verified_draft_content ?? "",
        draftFiles: buildHistoryDraftFileViewModels(detail.verified_files ?? []),
        steps: buildHistoryDetailStepViewModels(detail.steps),
        launchMode: detail.launch_mode ?? "new",
        sourceTaskId: detail.source_task_id ?? null,
        selectedProfileId: detail.selected_profile_id ?? null,
        requestedCapabilities: detail.requested_capabilities ?? [],
        appliedEvents: detail.applied_events ?? [],
        intent: detail.intent ?? null,
        reasoning: detail.reasoning ?? null,
        lastError: detail.last_error ?? null,
        appliedEventId:
            (detail.applied_events ?? []).find((item) => item.event_id)?.event_id ??
            detail.applied_event_id ??
            null,
    };
}

function validateDraftResult(
    result: CodingTaskDraftResult,
    originalTargetPath: string | null,
    originalContent: string | null,
): string | null {
    const primaryDraft = result.updated_file_content ?? result.verified_files?.[0]?.content ?? "";
    if (!primaryDraft.trim()) {
        return "This run did not produce an applicable change.";
    }
    const resolvedPrimaryPath =
        result.resolved_primary_target_path ?? result.verified_files?.[0]?.file_path ?? null;
    if (
        originalTargetPath &&
        originalContent &&
        resolvedPrimaryPath === originalTargetPath &&
        primaryDraft === originalContent
    ) {
        return "This run did not produce an applicable change.";
    }
    if (!result.intent.trim()) {
        return "This run did not produce an applicable change.";
    }
    return null;
}

function resolveDraftText(result: CodingTaskDraftResult): string {
    return result.updated_file_content ?? result.verified_files?.[0]?.content ?? "";
}

function formatTaskError(result: ApiResult<unknown>): string {
    if (result.ok) {
        return TASK_READY_MESSAGE;
    }
    if (typeof result.message === "string" && result.message.trim().length > 0) {
        return summarizeAssistantError(result.message);
    }
    switch (result.error) {
        case "backend_unavailable":
            return "TailEvents backend is unavailable.";
        case "timeout":
            return "Task request timed out.";
        default:
            return "This run did not complete.";
    }
}

function summarizeAssistantError(message: string): string {
    const normalized = message.trim();
    if (
        normalized === "This run did not produce an applicable change." ||
        normalized === "Model returned no edits" ||
        normalized === "Edit plan did not change any editable file" ||
        normalized === "Task returned an empty draft." ||
        normalized === "Task did not change the target file." ||
        normalized === "Task returned an empty intent."
    ) {
        return "This run did not produce an applicable change.";
    }
    if (!normalized) {
        return "This run did not complete.";
    }
    return "This run did not complete.";
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
    const summary = formatHistoryStepSummary(step);
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
