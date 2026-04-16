import { strict as assert } from "node:assert";
import path from "node:path";

import { TailEventsSidebarProvider } from "../src/sidebar-provider";
import type { CodingTaskSessionHandlers, TailEventsApi } from "../src/api-client";
import type {
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    BackendTailEvent,
    BackendTaskStepEvent,
    BackendToolCallPayload,
    CodingTaskCreateRequestPayload,
    CodingTaskDraftResult,
    CodingTaskToolResultPayload,
    CreateRawEventPayload,
    SidebarMessageToWebview,
} from "../src/types";

describe("TailEventsSidebarProvider", () => {
    const templatePath = path.join(__dirname, "..", "..", "media", "sidebar.html");

    it("replays mode, code state, and empty explain state when the webview becomes ready", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient(),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "ready" });
        await flushAsyncWork();

        assert.equal(findLastMessage(view.messages, "mode:update").mode, "explain");
        assert.equal(findLastMessage(view.messages, "code:update").data.canRun, true);
        assert.equal(findLastMessage(view.messages, "state:empty").message.includes("No entity selected"), true);
    });

    it("switches back to explain mode before showing an entity explicitly", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient(),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "setMode", mode: "code" });
        await provider.showExplainEntity("ent_1");

        assert.equal(findLastMessage(view.messages, "mode:update").mode, "explain");
        assert.equal(findLastMessage(view.messages, "state:update").data.entityId, "ent_1");
    });

    it("runs a coding task, handles a local view_file tool call, and exposes Apply after a verified draft", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, handlers) => {
                    handlers.onCreated?.("task_1");
                    handlers.onStatus?.("running");
                    handlers.onStep?.(sampleTaskStep("step_view", "view", "started"));
                    const toolResult = await handlers.onToolCall?.({
                        task_id: "task_1",
                        call_id: "call_1",
                        step_id: "step_view",
                        tool_name: "view_file",
                        file_path: "pkg/demo.py",
                        intent: "Observe the target file before editing",
                    } as BackendToolCallPayload);
                    assert.equal(toolResult?.content, "print(0)\n");
                    handlers.onStep?.(sampleTaskStep("step_view", "view", "succeeded"));
                    handlers.onStep?.(sampleTaskStep("step_edit", "edit", "started"));
                    handlers.onModelDelta?.('{"edits":[');
                    handlers.onModelDelta?.('{"old_text":"print(0)\\n"}]}');
                    handlers.onStep?.(sampleTaskStep("step_edit", "edit", "succeeded"));
                    handlers.onStep?.(sampleTaskStep("step_verify", "verify", "succeeded"));
                    handlers.onResult?.(sampleDraft());
                    handlers.onStatus?.("ready_to_apply");
                    return success(sampleDraft());
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({
            type: "runTask",
            prompt: "change output to 1",
            contextFiles: [],
        });
        await flushAsyncWork();

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "ready_to_apply");
        assert.equal(lastCodeUpdate.data.canApply, true);
        assert.equal(lastCodeUpdate.data.transcriptText.includes("tool_call: view_file pkg/demo.py"), true);
        assert.equal(lastCodeUpdate.data.modelOutputText.includes("--- attempt 1 ---"), true);
        assert.equal(lastCodeUpdate.data.modelOutputText.includes('{"edits":['), true);
        assert.equal(lastCodeUpdate.data.draftText, "print(1)\n");
    });

    it("shows the concrete step failure reason in the transcript", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, handlers) => {
                    handlers.onCreated?.("task_1");
                    handlers.onStatus?.("running");
                    handlers.onStep?.({
                        ...sampleTaskStep("step_edit", "edit", "failed"),
                        output_summary: "Edit plan did not change the target file",
                    });
                    return failure("unknown", "Edit plan did not change the target file");
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({
            type: "runTask",
            prompt: "change output to 1",
            contextFiles: [],
        });
        await flushAsyncWork();

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(
            lastCodeUpdate.data.transcriptText.includes(
                "edit/failed: pkg/demo.py - Edit plan did not change the target file",
            ),
            true,
        );
        assert.equal(lastCodeUpdate.data.message, "Edit plan did not change the target file");
    });

    it("rejects invalid context file selections before creating a task", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient(),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({
            type: "runTask",
            prompt: "change output to 1",
            contextFiles: ["pkg/demo.py"],
        });

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "error");
        assert.equal(lastCodeUpdate.data.message, "Context files must not include the target file.");
    });

    it("cancels an in-flight coding task and posts the cancelled message", async () => {
        let cancelledTaskId: string | null = null;
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, handlers, signal) => {
                    handlers.onCreated?.("task_1");
                    handlers.onStatus?.("running");
                    return new Promise((resolve) => {
                        signal?.addEventListener(
                            "abort",
                            () => resolve(failure("unknown")),
                            { once: true },
                        );
                    });
                },
                onCancelTask: async (taskId) => {
                    cancelledTaskId = taskId;
                    return success(null);
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        const runPromise = view.simulateMessage({
            type: "runTask",
            prompt: "change output to 1",
            contextFiles: [],
        });
        await flushAsyncWork();
        await view.simulateMessage({ type: "cancelTask" });
        await runPromise;

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "idle");
        assert.equal(lastCodeUpdate.data.message, "Task cancelled.");
        assert.equal(cancelledTaskId, "task_1");
    });

    it("writes the file, saves it, posts the final event, and refreshes explain on Apply", async () => {
        const operations: string[] = [];
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, handlers) => {
                    handlers.onCreated?.("task_1");
                    handlers.onResult?.(sampleDraft());
                    return success(sampleDraft());
                },
                onCreateEvent: async (_payload) => {
                    operations.push("event");
                    return success(sampleEvent());
                },
                entityByLocationResult: success(sampleEntity()),
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime({
                onReplace: () => operations.push("replace"),
                onSave: () => operations.push("save"),
            }),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({
            type: "runTask",
            prompt: "change output to 1",
            contextFiles: [],
        });
        await flushAsyncWork();
        await view.simulateMessage({ type: "applyTask" });
        await flushAsyncWork();

        assert.deepEqual(operations, ["replace", "save", "event"]);
        assert.equal(findLastMessage(view.messages, "state:update").data.entityId, "ent_1");
        assert.equal(findLastMessage(view.messages, "code:update").data.status, "applied");
    });
});

function createApiClient(options: {
    entityResult?: ApiResult<BackendCodeEntity>;
    entityByLocationResult?: ApiResult<BackendCodeEntity>;
    explanationResult?: ApiResult<BackendEntityExplanation>;
    eventsResult?: ApiResult<BackendTailEvent[]>;
    onCreateEvent?: (payload: CreateRawEventPayload) => Promise<ApiResult<BackendTailEvent>>;
    onRunTask?: (
        payload: CodingTaskCreateRequestPayload,
        handlers: CodingTaskSessionHandlers,
        signal?: AbortSignal,
    ) => Promise<ApiResult<CodingTaskDraftResult>>;
    onCancelTask?: (taskId: string) => Promise<ApiResult<null>>;
} = {}): TailEventsApi {
    return {
        getEntityByLocation: async () => options.entityByLocationResult ?? success(sampleEntity()),
        getEntity: async () => options.entityResult ?? success(sampleEntity()),
        getExplanationSummary: async () => success(sampleExplanation()),
        getExplanationFull: async () => options.explanationResult ?? success(sampleExplanation()),
        getEntityEvents: async () => options.eventsResult ?? success(sampleEvents()),
        createEvent: async (payload) => {
            if (options.onCreateEvent) {
                return options.onCreateEvent(payload);
            }
            return success(sampleEvent());
        },
        createCodingTask: async (_payload) => success({ task_id: "task_1", status: "created" }),
        submitCodingToolResult: async (_taskId, _payload) => success(null),
        cancelCodingTask: async (taskId) => {
            if (options.onCancelTask) {
                return options.onCancelTask(taskId);
            }
            return success(null);
        },
        runCodingTaskSession: async (payload, handlers, signal) => {
            if (options.onRunTask) {
                return options.onRunTask(payload, handlers, signal);
            }
            handlers.onCreated?.("task_1");
            handlers.onResult?.(sampleDraft());
            return success(sampleDraft());
        },
    };
}

function createRuntime(options: {
    activeEditor?: FakeEditor | null;
    fsPath?: string;
    languageId?: string;
    text?: string;
    isUntitled?: boolean;
    scheme?: string;
    workspaceFolders?: readonly any[];
    files?: Record<string, string>;
    onReplace?: () => void;
    onSave?: () => void;
    saveResult?: boolean;
} = {}) {
    const document = new FakeDocument({
        fsPath: options.fsPath ?? "C:\\repo\\demo\\pkg\\demo.py",
        languageId: options.languageId ?? "python",
        text: options.text ?? "print(0)\n",
        isUntitled: options.isUntitled ?? false,
        scheme: options.scheme ?? "file",
    });
    const editor = options.activeEditor === undefined ? new FakeEditor(document) : options.activeEditor;
    const files: Record<string, string> = {
        "C:\\repo\\demo\\pkg\\demo.py": "print(0)\n",
        "C:\\repo\\demo\\pkg\\context.py": "VALUE = 1\n",
        ...(options.files ?? {}),
    };

    return {
        getActiveEditor: () => editor as unknown as any,
        getWorkspaceFolders: () => options.workspaceFolders ?? [createWorkspaceFolder("C:\\repo\\demo")],
        resolveWorkspaceRelativePath: (absolutePath: string) => {
            const normalized = absolutePath.replace(/\\/g, "/");
            const prefix = "C:/repo/demo/";
            if (!normalized.startsWith(prefix)) {
                return null;
            }
            return normalized.slice(prefix.length);
        },
        resolveAbsoluteWorkspacePath: (workspaceFilePath: string) => {
            const candidate = `C:\\repo\\demo\\${workspaceFilePath.replace(/\//g, "\\")}`;
            return Object.prototype.hasOwnProperty.call(files, candidate) ? candidate : null;
        },
        getOpenDocumentByAbsolutePath: (absolutePath: string) => {
            if (absolutePath === document.uri.fsPath) {
                return document as unknown as any;
            }
            return null;
        },
        readFileText: async (absolutePath: string) => files[absolutePath] ?? "",
        replaceDocumentContent: async (_editor: unknown, content: string) => {
            options.onReplace?.();
            document.text = content;
            return true;
        },
        saveDocument: async () => {
            options.onSave?.();
            return options.saveResult ?? true;
        },
    };
}

class FakeDocument {
    public version = 1;

    public readonly isUntitled: boolean;

    public readonly uri: { fsPath: string; scheme: string };

    public readonly languageId: string;

    public text: string;

    public constructor(options: {
        fsPath: string;
        languageId: string;
        text: string;
        isUntitled: boolean;
        scheme: string;
    }) {
        this.languageId = options.languageId;
        this.text = options.text;
        this.isUntitled = options.isUntitled;
        this.uri = {
            fsPath: options.fsPath,
            scheme: options.scheme,
        };
    }

    public getText(): string {
        return this.text;
    }
}

class FakeEditor {
    public readonly selection = {
        active: {
            line: 0,
        },
    };

    public constructor(public readonly document: FakeDocument) {}
}

class FakeWebviewView {
    public readonly messages: SidebarMessageToWebview[] = [];

    private messageListener: ((message: unknown) => void | Promise<void>) | null = null;

    public readonly webview = {
        cspSource: "vscode-webview://tail-events",
        html: "",
        options: undefined as unknown,
        onDidReceiveMessage: (listener: (message: unknown) => void | Promise<void>) => {
            this.messageListener = listener;
            return {
                dispose() {
                    return;
                },
            };
        },
        postMessage: async (message: SidebarMessageToWebview) => {
            this.messages.push(message);
            return true;
        },
    };

    public asView() {
        return {
            onDidChangeVisibility() {
                return { dispose() { return; } };
            },
            onDidDispose() {
                return { dispose() { return; } };
            },
            show() {
                return;
            },
            viewType: "tailevents.sidebarView",
            visible: true,
            webview: this.webview,
        };
    }

    public async simulateMessage(message: unknown): Promise<void> {
        await this.messageListener?.(message);
    }
}

function sampleEntity(): BackendCodeEntity {
    return {
        entity_id: "ent_1",
        name: "process_data",
        qualified_name: "pkg.process_data",
        entity_type: "function",
        file_path: "pkg/demo.py",
        line_range: [10, 20],
        signature: "def process_data(value: str) -> str",
        params: [],
        return_type: "str",
        docstring: null,
        created_at: "2026-04-15T00:00:00Z",
        created_by_event: "te_1",
        last_modified_event: "te_2",
        last_modified_at: "2026-04-15T00:02:00Z",
        modification_count: 2,
        is_deleted: false,
        deleted_by_event: null,
        event_refs: [
            {
                event_id: "te_1",
                role: "primary",
                timestamp: "2026-04-15T00:00:00Z",
            },
        ],
        rename_history: [],
        is_external: false,
        package: null,
        cached_description: null,
        description_valid: false,
        in_degree: 0,
        out_degree: 1,
        tags: [],
    };
}

function sampleExplanation(): BackendEntityExplanation {
    return {
        entity_id: "ent_1",
        entity_name: "process_data",
        qualified_name: "pkg.process_data",
        entity_type: "function",
        signature: "def process_data(value: str) -> str",
        summary: "Normalize the input before downstream processing.",
        detailed_explanation: "Normalize the input before downstream processing.",
        param_explanations: null,
        return_explanation: null,
        usage_context: null,
        creation_intent: null,
        modification_history: [],
        related_entities: [],
        external_doc_snippets: [],
        generated_at: "2026-04-15T00:00:00Z",
        from_cache: false,
        confidence: 1,
    };
}

function sampleEvents(): BackendTailEvent[] {
    return [
        {
            event_id: "te_1",
            timestamp: "2026-04-15T00:00:00Z",
            agent_step_id: null,
            session_id: null,
            action_type: "modify",
            file_path: "pkg/demo.py",
            line_range: null,
            code_snapshot: "print(1)\n",
            intent: "change output",
            reasoning: null,
            decision_alternatives: null,
            entity_refs: [],
            external_refs: [],
        },
    ];
}

function sampleEvent(): BackendTailEvent {
    return sampleEvents()[0];
}

function sampleDraft(): CodingTaskDraftResult {
    return {
        task_id: "task_1",
        updated_file_content: "print(1)\n",
        intent: "change output to 1",
        reasoning: "minimal edit",
        session_id: "task_1",
        agent_step_id: "step_verify",
        action_type: "modify",
    };
}

function sampleTaskStep(
    stepId: string,
    stepKind: "view" | "edit" | "verify",
    status: "started" | "succeeded" | "failed",
): BackendTaskStepEvent {
    return {
        task_id: "task_1",
        step_id: stepId,
        step_kind: stepKind,
        status,
        file_path: "pkg/demo.py",
        content_hash: "hash",
        intent: `${stepKind} step`,
        reasoning_summary: null,
        tool_name: stepKind === "view" ? "view_file" : null,
        input_summary: "input",
        output_summary: "output",
        timestamp: "2026-04-15T00:00:00Z",
    };
}

function createWorkspaceFolder(fsPath: string) {
    return {
        name: "demo",
        index: 0,
        uri: {
            fsPath,
        },
    } as any;
}

function success<T>(data: T): ApiResult<T> {
    return {
        ok: true,
        data,
        status: 200,
    };
}

function failure<T>(error: "backend_unavailable" | "entity_not_found" | "timeout" | "unknown", message?: string): ApiResult<T> {
    return {
        ok: false,
        error,
        status: null,
        ...(message ? { message } : {}),
    };
}

async function flushAsyncWork(): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 0));
}

function findLastMessage<TType extends SidebarMessageToWebview["type"]>(
    messages: SidebarMessageToWebview[],
    type: TType,
): Extract<SidebarMessageToWebview, { type: TType }> {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (message.type === type) {
            return message as Extract<SidebarMessageToWebview, { type: TType }>;
        }
    }
    throw new Error(`Message ${type} not found`);
}
