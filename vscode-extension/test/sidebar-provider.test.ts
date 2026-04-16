import { strict as assert } from "node:assert";
import path from "node:path";

import { TailEventsSidebarProvider } from "../src/sidebar-provider";
import type { TailEventsApi } from "../src/api-client";
import type {
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    BackendTailEvent,
    CodingTaskResult,
    CreateRawEventPayload,
    SidebarMessageToWebview,
} from "../src/types";

describe("TailEventsSidebarProvider", () => {
    const templatePath = path.join(__dirname, "..", "..", "media", "sidebar.html");

    it("replays mode, code state, and empty explain state when the webview becomes ready", async () => {
        const runtime = createRuntime();
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient(),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "ready" });

        assert.equal(view.messages[0].type, "mode:update");
        assert.equal(view.messages[1].type, "code:update");
        assert.equal(view.messages[2].type, "state:empty");
    });

    it("posts loading then update when entity and explanation succeed", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient(),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await provider.loadEntity("ent_1");

        assert.equal(view.messages[0].type, "state:loading");
        assert.equal(view.messages[1].type, "state:update");

        const update = view.messages[1];
        assert.equal(update.type, "state:update");
        assert.equal(update.data.timeline[0].eventId, "te_2");
        assert.equal(update.data.timeline[0].renameLabel, "Renamed from pkg.old_name to pkg.process_data");
        assert.equal(update.data.relatedEntities[0].entityId, "ent_2");
    });

    it("keeps the panel usable when event history fails", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                eventsResult: failure("unknown"),
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await provider.loadEntity("ent_1");

        const update = view.messages[1];
        assert.equal(update.type, "state:update");
        assert.equal(update.data.historyAvailable, false);
        assert.equal(update.data.timeline.length, 0);
    });

    it("posts an error state when required data fails", async () => {
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                entityResult: failure("entity_not_found"),
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await provider.loadEntity("ent_missing");

        const error = view.messages[1];
        assert.equal(error.type, "state:error");
        assert.equal(error.error, "entity_not_found");
    });

    it("ignores stale responses after a newer load aborts the previous one", async () => {
        const entityDeferred = createAbortableDeferred<BackendCodeEntity>();
        const explanationDeferred = createAbortableDeferred<BackendEntityExplanation>();
        const eventsDeferred = createAbortableDeferred<BackendTailEvent[]>();
        const provider = new TailEventsSidebarProvider({
            apiClient: {
                ...createApiClient(),
                getEntity: async (entityId, signal) => {
                    if (entityId === "ent_slow") {
                        return entityDeferred.promise(signal);
                    }
                    return success(sampleEntity("ent_fast"));
                },
                getExplanationFull: async (entityId, signal) => {
                    if (entityId === "ent_slow") {
                        return explanationDeferred.promise(signal);
                    }
                    return success(sampleExplanation("ent_fast"));
                },
                getEntityEvents: async (entityId, signal) => {
                    if (entityId === "ent_slow") {
                        return eventsDeferred.promise(signal);
                    }
                    return success(sampleEvents());
                },
            },
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime: createRuntime(),
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        const firstLoad = provider.loadEntity("ent_slow");
        const secondLoad = provider.loadEntity("ent_fast");

        await Promise.all([firstLoad, secondLoad]);

        const updateMessages = view.messages.filter((message) => message.type === "state:update");
        assert.equal(updateMessages.length, 1);
        assert.equal(updateMessages[0].data.entityId, "ent_fast");
    });

    it("runs a coding task and exposes apply when a valid result arrives", async () => {
        const runtime = createRuntime();
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, handlers) => {
                    handlers.onDelta?.('{"updated');
                    handlers.onDelta?.('_file_content":"print(1)\\n"}');
                    return success({
                        updated_file_content: "print(1)\n",
                        intent: "change output to 1",
                        reasoning: "minimal edit",
                        action_type: "modify",
                    });
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
            generateSessionId: () => "b0_fixed123456",
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "runTask", prompt: "change output to 1" });

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "ready_to_apply");
        assert.equal(lastCodeUpdate.data.canApply, true);
        assert.ok(lastCodeUpdate.data.streamedText.includes('{"updated'));
    });

    it("cancels an in-flight coding task", async () => {
        let aborted = false;
        const runtime = createRuntime();
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async (_payload, _handlers, signal) => {
                    return new Promise((resolve) => {
                        signal?.addEventListener(
                            "abort",
                            () => {
                                aborted = true;
                                resolve(failure("unknown"));
                            },
                            { once: true },
                        );
                    });
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        const running = view.simulateMessage(
            { type: "runTask", prompt: "change output to 1" },
            false,
        );
        await flushAsyncWork();
        await view.simulateMessage({ type: "cancelTask" });
        await running;

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(aborted, true);
        assert.equal(lastCodeUpdate.data.status, "idle");
        assert.equal(lastCodeUpdate.data.message, "Task cancelled.");
    });

    it("applies generated content, writes a RawEvent, and refreshes explain", async () => {
        const runtime = createRuntime();
        const createdEvents: CreateRawEventPayload[] = [];
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async () => {
                    return success({
                        updated_file_content: "print(1)\n",
                        intent: "change output to 1",
                        reasoning: "minimal edit",
                        action_type: "modify",
                    });
                },
                onCreateEvent: async (payload) => {
                    createdEvents.push(payload);
                    return success(sampleEvents()[0]);
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
            generateSessionId: () => "b0_fixed123456",
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "runTask", prompt: "change output to 1" });
        await view.simulateMessage({ type: "applyTask" });

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        const explainUpdate = findLastMessage(view.messages, "state:update");
        assert.equal(runtime.editor.document.text, "print(1)\n");
        assert.equal(createdEvents.length, 1);
        assert.equal(createdEvents[0].file_path, "pkg/demo.py");
        assert.equal(createdEvents[0].session_id, "b0_fixed123456");
        assert.equal(lastCodeUpdate.data.status, "applied");
        assert.equal(explainUpdate.data.entityId, "ent_1");
    });

    it("rejects apply when the file changed after generation", async () => {
        const runtime = createRuntime();
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async () => {
                    return success({
                        updated_file_content: "print(1)\n",
                        intent: "change output to 1",
                        reasoning: null,
                        action_type: "modify",
                    });
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "runTask", prompt: "change output to 1" });
        runtime.editor.document.version += 1;
        await view.simulateMessage({ type: "applyTask" });

        const lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "error");
        assert.equal(lastCodeUpdate.data.message, "The file changed after generation. Please run again.");
    });

    it("keeps retry event write available when event creation fails", async () => {
        const runtime = createRuntime();
        let createEventCalls = 0;
        const provider = new TailEventsSidebarProvider({
            apiClient: createApiClient({
                onRunTask: async () => {
                    return success({
                        updated_file_content: "print(1)\n",
                        intent: "change output to 1",
                        reasoning: null,
                        action_type: "modify",
                    });
                },
                onCreateEvent: async () => {
                    createEventCalls += 1;
                    if (createEventCalls === 1) {
                        return failure("unknown");
                    }
                    return success(sampleEvents()[0]);
                },
            }),
            templatePath,
            getBaseUrl: () => "http://127.0.0.1:8766/api/v1",
            runtime,
        });
        const view = new FakeWebviewView();

        provider.resolveWebviewView(view.asView() as never);
        await view.simulateMessage({ type: "runTask", prompt: "change output to 1" });
        await view.simulateMessage({ type: "applyTask" });

        let lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(lastCodeUpdate.data.status, "error");
        assert.equal(lastCodeUpdate.data.canRetryEventWrite, true);
        assert.equal(runtime.editor.document.text, "print(1)\n");

        await view.simulateMessage({ type: "retryEventWrite" });

        lastCodeUpdate = findLastMessage(view.messages, "code:update");
        assert.equal(createEventCalls, 2);
        assert.equal(lastCodeUpdate.data.status, "applied");
    });
});

function createApiClient(options: {
    entityResult?: ApiResult<BackendCodeEntity>;
    explanationResult?: ApiResult<BackendEntityExplanation>;
    eventsResult?: ApiResult<BackendTailEvent[]>;
    onRunTask?: TailEventsApi["runCodingTaskStream"];
    onCreateEvent?: TailEventsApi["createEvent"];
} = {}): TailEventsApi {
    return {
        getEntityByLocation: async () => success(sampleEntity()),
        getEntity: async () => options.entityResult ?? success(sampleEntity()),
        getExplanationSummary: async () => success(sampleExplanation()),
        getExplanationFull: async () => options.explanationResult ?? success(sampleExplanation()),
        getEntityEvents: async () => options.eventsResult ?? success(sampleEvents()),
        createEvent: async (payload, signal) => {
            if (options.onCreateEvent) {
                return options.onCreateEvent(payload, signal);
            }
            return success(sampleEvents()[0]);
        },
        runCodingTaskStream: async (payload, handlers, signal) => {
            if (options.onRunTask) {
                return options.onRunTask(payload, handlers, signal);
            }
            handlers.onDelta?.('{"updated_file_content":"print(1)\\n"}');
            return success({
                updated_file_content: "print(1)\n",
                intent: "change output to 1",
                reasoning: null,
                action_type: "modify",
            });
        },
    };
}

function createRuntime() {
    const document = new FakeDocument("C:\\repo\\demo\\pkg\\demo.py", "python", "print(0)\n");
    const editor = new FakeEditor(document);
    return {
        editor,
        getActiveEditor: () => editor as unknown as any,
        getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
        replaceDocumentContent: async (_editor: unknown, content: string) => {
            document.text = content;
            document.version += 1;
            return true;
        },
        saveDocument: async () => true,
    };
}

class FakeDocument {
    public version = 1;

    public readonly isUntitled = false;

    public readonly uri: { fsPath: string; scheme: string };

    public constructor(
        fsPath: string,
        public readonly languageId: string,
        public text: string,
    ) {
        this.uri = {
            fsPath,
            scheme: "file",
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

    public async simulateMessage(message: unknown, waitForIdle = true): Promise<void> {
        this.messageListener?.(message);
        if (waitForIdle) {
            await flushAsyncWork();
        }
    }
}

function createAbortableDeferred<T>() {
    return {
        promise(signal?: AbortSignal): Promise<ApiResult<T>> {
            return new Promise((resolve) => {
                if (signal?.aborted) {
                    resolve(failure("unknown"));
                    return;
                }
                signal?.addEventListener(
                    "abort",
                    () => {
                        resolve(failure("unknown"));
                    },
                    { once: true },
                );
            });
        },
    };
}

function sampleEntity(entityId = "ent_1"): BackendCodeEntity {
    return {
        entity_id: entityId,
        name: entityId === "ent_2" ? "helper" : "process_data",
        qualified_name: entityId === "ent_2" ? "pkg.helper" : "pkg.process_data",
        entity_type: "function",
        file_path: "pkg/demo.py",
        line_range: [10, 20],
        signature: entityId === "ent_2" ? "def helper() -> None" : "def process_data(value: str) -> str",
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
            {
                event_id: "te_2",
                role: "modified",
                timestamp: "2026-04-15T00:02:00Z",
            },
        ],
        rename_history: [
            {
                old_qualified_name: "pkg.old_name",
                new_qualified_name: "pkg.process_data",
                event_id: "te_2",
                timestamp: "2026-04-15T00:02:00Z",
            },
        ],
        is_external: false,
        package: null,
        cached_description: null,
        description_valid: false,
        in_degree: 0,
        out_degree: 1,
        tags: [],
    };
}

function sampleExplanation(entityId = "ent_1"): BackendEntityExplanation {
    return {
        entity_id: entityId,
        entity_name: entityId === "ent_2" ? "helper" : "process_data",
        qualified_name: entityId === "ent_2" ? "pkg.helper" : "pkg.process_data",
        entity_type: "function",
        signature: entityId === "ent_2" ? "def helper() -> None" : "def process_data(value: str) -> str",
        summary: entityId === "ent_2"
            ? "Support the main processing flow."
            : "Normalize the input before downstream processing.",
        detailed_explanation: entityId === "ent_2"
            ? "Support the main processing flow."
            : "Normalize the input before downstream processing.",
        param_explanations: null,
        return_explanation: null,
        usage_context: null,
        creation_intent: null,
        modification_history: [],
        related_entities: [
            {
                entity_id: "ent_2",
                entity_name: "helper",
                qualified_name: "pkg.helper",
                entity_type: "function",
                direction: "outgoing",
                relation_type: "calls",
                confidence: 1,
                context: null,
            },
        ],
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
            session_id: "session_1",
            action_type: "create",
            file_path: "pkg/demo.py",
            line_range: [10, 15],
            code_snapshot: "def process_data(): ...",
            intent: "Create process_data",
            reasoning: null,
            decision_alternatives: null,
            entity_refs: [],
            external_refs: [],
        },
        {
            event_id: "te_2",
            timestamp: "2026-04-15T00:02:00Z",
            agent_step_id: null,
            session_id: "session_1",
            action_type: "rename",
            file_path: "pkg/demo.py",
            line_range: [10, 20],
            code_snapshot: "def process_data(value): ...",
            intent: "Rename and expand process_data",
            reasoning: null,
            decision_alternatives: null,
            entity_refs: [],
            external_refs: [],
        },
    ];
}

function findLastMessage<T extends SidebarMessageToWebview["type"]>(
    messages: SidebarMessageToWebview[],
    type: T,
): Extract<SidebarMessageToWebview, { type: T }> {
    const message = [...messages].reverse().find((item) => item.type === type);
    assert.ok(message);
    return message as Extract<SidebarMessageToWebview, { type: T }>;
}

async function flushAsyncWork(): Promise<void> {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
}

function success<T>(data: T): ApiResult<T> {
    return {
        ok: true,
        data,
        status: 200,
    };
}

function failure<T>(error: "backend_unavailable" | "entity_not_found" | "timeout" | "unknown"): ApiResult<T> {
    return {
        ok: false,
        error,
        status: null,
    };
}
