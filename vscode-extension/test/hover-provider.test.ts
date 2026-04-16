import { strict as assert } from "node:assert";

import { TailEventsHoverProvider } from "../src/hover-provider";
import type { TailEventsApi } from "../src/api-client";
import type {
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    CodingTaskCreateRequestPayload,
    CodingTaskDraftResult,
    CodingTaskToolResultPayload,
    CreateRawEventPayload,
} from "../src/types";

describe("TailEventsHoverProvider", () => {
    it("returns a full hover when the entity and summary are available", async () => {
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: success(sampleEntity()),
                summaryResult: success(sampleExplanation()),
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
            isHoverEnabled: () => true,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\pkg\\demo.py", true) as never,
            { line: 9 } as never,
            createToken() as never,
        ) as unknown as FakeHover | null;

        assert.ok(hover);
        assert.ok(hover.contents.value.includes("process_data"));
        assert.ok(hover.contents.value.includes("Normalize the input"));
        assert.ok(hover.contents.value.includes("command:tailEvents.explainCurrentSymbol?"));
        assert.deepEqual(hover.contents.isTrusted.enabledCommands, ["tailEvents.explainCurrentSymbol"]);
    });

    it("returns a minimal hover when summary lookup fails", async () => {
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: success(sampleEntity()),
                summaryResult: failure("unknown"),
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
            isHoverEnabled: () => true,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\pkg\\demo.py", true) as never,
            { line: 9 } as never,
            createToken() as never,
        ) as unknown as FakeHover | null;

        assert.ok(hover);
        assert.ok(!hover.contents.value.includes("Normalize the input"));
        assert.ok(hover.contents.value.includes("1 events recorded"));
    });

    it("returns null when hover preview is disabled", async () => {
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: success(sampleEntity()),
                summaryResult: success(sampleExplanation()),
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
            isHoverEnabled: () => false,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\pkg\\demo.py", true) as never,
            { line: 9 } as never,
            createToken() as never,
        );

        assert.equal(hover, null);
    });

    it("returns null when there is no word range at the hover position", async () => {
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: success(sampleEntity()),
                summaryResult: success(sampleExplanation()),
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
            isHoverEnabled: () => true,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\pkg\\demo.py", false) as never,
            { line: 9 } as never,
            createToken() as never,
        );

        assert.equal(hover, null);
    });

    it("returns null when the entity lookup fails", async () => {
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: failure("entity_not_found"),
                summaryResult: success(sampleExplanation()),
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo" } } as any],
            isHoverEnabled: () => true,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\pkg\\demo.py", true) as never,
            { line: 9 } as never,
            createToken() as never,
        );

        assert.equal(hover, null);
    });

    it("falls back to a workspace-folder-prefixed file path when the plain relative path misses", async () => {
        const requestedFiles: string[] = [];
        const provider = new TailEventsHoverProvider({
            apiClient: createApiClient({
                entityResult: success(sampleEntity()),
                summaryResult: success(sampleExplanation()),
                onEntityLookup: (file) => {
                    requestedFiles.push(file);
                    if (file === "manual_test_target.py") {
                        return failure("entity_not_found");
                    }
                    return success(sampleEntity());
                },
            }),
            getWorkspaceFolders: () => [{ uri: { fsPath: "C:\\repo\\demo\\vscode-extension" } } as any],
            isHoverEnabled: () => true,
            vscodeApi: {
                Hover: FakeHover,
                MarkdownString: FakeMarkdownString,
            },
        });

        const hover = await provider.provideHover(
            createDocument("C:\\repo\\demo\\vscode-extension\\manual_test_target.py", true) as never,
            { line: 0 } as never,
            createToken() as never,
        ) as unknown as FakeHover | null;

        assert.ok(hover);
        assert.deepEqual(requestedFiles, [
            "manual_test_target.py",
            "vscode-extension/manual_test_target.py",
        ]);
        assert.ok(hover.contents.value.includes("View Details"));
    });
});

function createApiClient(options: {
    entityResult: ApiResult<BackendCodeEntity>;
    summaryResult: ApiResult<BackendEntityExplanation>;
    onEntityLookup?: (file: string, line: number) => ApiResult<BackendCodeEntity>;
}): TailEventsApi {
    return {
        getEntityByLocation: async (file, line) => {
            if (options.onEntityLookup) {
                return options.onEntityLookup(file, line);
            }
            return options.entityResult;
        },
        getEntity: async () => success(sampleEntity()),
        getExplanationSummary: async () => options.summaryResult,
        getExplanationFull: async () => success(sampleExplanation()),
        getEntityEvents: async () => success([]),
        createEvent: async (_payload: CreateRawEventPayload) => success(sampleEvent()),
        onboardBaselineFile: async () => success({
            status: "created",
            file_path: "pkg/demo.py",
            event_id: "te_onboard",
            reason: null,
        }),
        createCodingTask: async (_payload: CodingTaskCreateRequestPayload) => success({ task_id: "task_1", status: "created" }),
        submitCodingToolResult: async (_taskId: string, _payload: CodingTaskToolResultPayload) => success(null),
        cancelCodingTask: async () => success(null),
        runCodingTaskSession: async (_payload, _handlers) => success(sampleDraft()),
    };
}

function createDocument(fsPath: string, hasWordRange: boolean) {
    return {
        fileName: fsPath,
        isUntitled: false,
        languageId: "python",
        uri: {
            fsPath,
            scheme: "file",
        },
        getWordRangeAtPosition: () => (hasWordRange ? { start: 0, end: 1 } : undefined),
    };
}

function createToken() {
    return {
        isCancellationRequested: false,
        onCancellationRequested: () => ({
            dispose() {
                return;
            },
        }),
    };
}

class FakeMarkdownString {
    public value = "";

    public isTrusted: any;

    public supportHtml = false;

    public constructor(initialValue = "") {
        this.value = initialValue;
    }

    public appendMarkdown(markdown: string): void {
        this.value += markdown;
    }

    public appendText(text: string): void {
        this.value += text;
    }

    public appendCodeblock(code: string): void {
        this.value += code;
    }
}

class FakeHover {
    public constructor(
        public readonly contents: FakeMarkdownString,
        public readonly range: unknown,
    ) {}
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
        last_modified_event: "te_1",
        last_modified_at: "2026-04-15T00:00:00Z",
        modification_count: 1,
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
        out_degree: 0,
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

function sampleEvent() {
    return {
        event_id: "te_1",
        timestamp: "2026-04-15T00:00:00Z",
        agent_step_id: null,
        session_id: null,
        action_type: "modify",
        file_path: "pkg/demo.py",
        line_range: null,
        code_snapshot: "print(1)\n",
        intent: "update output",
        reasoning: null,
        decision_alternatives: null,
        entity_refs: [],
        external_refs: [],
    };
}

function sampleDraft(): CodingTaskDraftResult {
    return {
        task_id: "task_1",
        updated_file_content: "print(1)\n",
        intent: "update output",
        reasoning: null,
        session_id: "task_1",
        agent_step_id: "step_verify",
        action_type: "modify",
    };
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
