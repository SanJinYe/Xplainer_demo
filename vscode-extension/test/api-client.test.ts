import { strict as assert } from "node:assert";

import { TailEventsApiClient } from "../src/api-client";
import type {
    BaselineOnboardFileResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    BackendTaskStepEvent,
    BackendToolCallPayload,
    CodingTaskDraftResult,
    CodingTaskToolResultPayload,
} from "../src/types";

describe("TailEventsApiClient", () => {
    it("caches entity lookups for 30 seconds and normalizes the base URL", async () => {
        let fetchCount = 0;
        let now = 1_000;
        let lastUrl = "";

        const client = createClient(async (url) => {
            fetchCount += 1;
            lastUrl = String(url);
            return jsonResponse(sampleEntity());
        }, () => now);

        const first = await client.getEntityByLocation("pkg/demo.py", 12);
        const second = await client.getEntityByLocation("pkg/demo.py", 12);
        now += 30_001;
        const third = await client.getEntityByLocation("pkg/demo.py", 12);

        assert.equal(first.ok, true);
        assert.equal(second.ok, true);
        assert.equal(third.ok, true);
        assert.equal(fetchCount, 2);
        assert.equal(
            lastUrl,
            "http://127.0.0.1:8766/api/v1/entities/by-location?file=pkg%2Fdemo.py&line=12",
        );
    });

    it("caches summary lookups independently by entity id", async () => {
        let fetchCount = 0;
        let now = 2_000;

        const client = createClient(async () => {
            fetchCount += 1;
            return jsonResponse(sampleExplanation());
        }, () => now);

        const first = await client.getExplanationSummary("ent_1");
        const second = await client.getExplanationSummary("ent_1");
        const third = await client.getExplanationSummary("ent_2");

        assert.equal(first.ok, true);
        assert.equal(second.ok, true);
        assert.equal(third.ok, true);
        assert.equal(fetchCount, 2);
    });

    it("does not negatively cache 404 responses", async () => {
        let fetchCount = 0;
        const client = createClient(async () => {
            fetchCount += 1;
            return new Response("{}", { status: 404 });
        });

        const first = await client.getEntityByLocation("pkg/demo.py", 1);
        const second = await client.getEntityByLocation("pkg/demo.py", 1);

        assert.equal(first.ok, false);
        assert.equal(second.ok, false);
        assert.equal(fetchCount, 2);
        assert.equal(first.ok ? "" : first.error, "entity_not_found");
    });

    it("posts baseline onboarding requests to the dedicated route", async () => {
        let requestUrl = "";
        let requestBody: unknown;

        const client = createClient(async (url, init) => {
            requestUrl = String(url);
            requestBody = parseJsonBody(init?.body);
            return jsonResponse({
                status: "created",
                file_path: "pkg/demo.py",
                event_id: "te_baseline",
                reason: null,
            } satisfies BaselineOnboardFileResult);
        });

        const result = await client.onboardBaselineFile({
            file_path: "pkg/demo.py",
            code_snapshot: "print(1)\n",
        });

        assert.equal(result.ok, true);
        assert.equal(
            requestUrl,
            "http://127.0.0.1:8766/api/v1/baseline/onboard-file",
        );
        assert.deepEqual(requestBody, {
            file_path: "pkg/demo.py",
            code_snapshot: "print(1)\n",
        });
    });

    it("classifies connection failures as backend unavailable", async () => {
        const client = createClient(async () => {
            throw new TypeError("fetch failed");
        });

        const result = await client.getEntity("ent_1");

        assert.equal(result.ok, false);
        assert.equal(result.ok ? "" : result.error, "backend_unavailable");
    });

    it("classifies server errors as unknown", async () => {
        const client = createClient(async () => {
            return new Response("{}", { status: 500 });
        });

        const result = await client.getEntity("ent_1");

        assert.equal(result.ok, false);
        assert.equal(result.ok ? "" : result.error, "unknown");
    });

    it("classifies timeout errors correctly", async () => {
        const client = createClient(
            (_url, init) => {
                return new Promise<Response>((_resolve, reject) => {
                    init?.signal?.addEventListener(
                        "abort",
                        () => {
                            reject(init.signal?.reason);
                        },
                        { once: true },
                    );
                });
            },
            undefined,
            () => 1_000,
            5,
        );

        const result = await client.getEntity("ent_1");

        assert.equal(result.ok, false);
        assert.equal(result.ok ? "" : result.error, "timeout");
    });

    it("propagates caller cancellation to the fetch signal", async () => {
        const controller = new AbortController();
        let signalAborted = false;

        const client = createClient((_url, init) => {
            return new Promise<Response>((_resolve, reject) => {
                init?.signal?.addEventListener(
                    "abort",
                    () => {
                        signalAborted = true;
                        reject(init.signal?.reason);
                    },
                    { once: true },
                );
                controller.abort();
            });
        });

        const result = await client.getEntity("ent_1", controller.signal);

        assert.equal(signalAborted, true);
        assert.equal(result.ok, false);
    });

    it("runs a coding task session through create, tool_result, and verified draft events", async () => {
        const requests: Array<{ url: string; method: string; body?: unknown }> = [];
        const statuses: string[] = [];
        const steps: BackendTaskStepEvent[] = [];
        const modelDeltas: string[] = [];
        const toolCalls: BackendToolCallPayload[] = [];
        const results: CodingTaskDraftResult[] = [];

        const client = createClient(async (url, init) => {
            const requestUrl = String(url);
            const method = init?.method ?? "GET";
            requests.push({
                url: requestUrl,
                method,
                body: parseJsonBody(init?.body),
            });

            if (requestUrl.endsWith("/coding/tasks") && method === "POST") {
                return jsonResponse({ task_id: "task_1", status: "created" }, 201);
            }
            if (requestUrl.endsWith("/coding/tasks/task_1/stream") && method === "GET") {
                return sseResponse(
                    [
                        {
                            event: "status",
                            data: { status: "running" },
                        },
                        {
                            event: "step",
                            data: sampleTaskStep(),
                        },
                        {
                            event: "model_delta",
                            data: { text: "{\"edits\":[" },
                        },
                        {
                            event: "tool_call",
                            data: sampleToolCall(),
                        },
                        {
                            event: "result",
                            data: sampleDraft(),
                        },
                        {
                            event: "done",
                            data: {},
                        },
                    ],
                );
            }
            if (requestUrl.endsWith("/coding/tasks/task_1/tool-result") && method === "POST") {
                return new Response(null, { status: 204 });
            }
            throw new Error(`Unexpected request: ${method} ${requestUrl}`);
        });

        const outcome = await client.runCodingTaskSession(
            {
                target_file_path: "pkg/demo.py",
                target_file_version: 1,
                user_prompt: "change output to 1",
                context_files: [],
            },
            {
                onCreated: (taskId) => statuses.push(`created:${taskId}`),
                onStatus: (status) => statuses.push(status),
                onStep: (step) => steps.push(step),
                onModelDelta: (text) => modelDeltas.push(text),
                onToolCall: async (payload) => {
                    toolCalls.push(payload);
                    return {
                        call_id: payload.call_id,
                        tool_name: "view_file",
                        file_path: payload.file_path,
                        document_version: 1,
                        content: "print(0)\n",
                        content_hash: "hash",
                        error: null,
                    };
                },
                onResult: (result) => results.push(result),
            },
        );

        assert.equal(outcome.ok, true);
        assert.deepEqual(statuses, ["created:task_1", "running"]);
        assert.equal(steps.length, 1);
        assert.deepEqual(modelDeltas, ['{"edits":[']);
        assert.equal(toolCalls.length, 1);
        assert.equal(results.length, 1);
        assert.equal(
            requests.some((request) => {
                return (
                    request.method === "POST" &&
                    request.url.endsWith("/coding/tasks/task_1/tool-result") &&
                    (request.body as CodingTaskToolResultPayload).content === "print(0)\n"
                );
            }),
            true,
        );
    });

    it("preserves backend error messages from the coding task stream", async () => {
        const client = createClient(async (url, init) => {
            const requestUrl = String(url);
            const method = init?.method ?? "GET";

            if (requestUrl.endsWith("/coding/tasks") && method === "POST") {
                return jsonResponse({ task_id: "task_1", status: "created" }, 201);
            }
            if (requestUrl.endsWith("/coding/tasks/task_1/stream") && method === "GET") {
                return sseResponse(
                    [
                        {
                            event: "error",
                            data: { message: "Draft is not valid Python: line 1: invalid syntax" },
                        },
                        {
                            event: "done",
                            data: {},
                        },
                    ],
                );
            }
            throw new Error(`Unexpected request: ${method} ${requestUrl}`);
        });

        const outcome = await client.runCodingTaskSession(
            {
                target_file_path: "pkg/demo.py",
                target_file_version: 1,
                user_prompt: "change output to 1",
                context_files: [],
            },
            {},
        );

        assert.equal(outcome.ok, false);
        if (outcome.ok) {
            throw new Error("Expected a failed result");
        }
        assert.equal(outcome.error, "unknown");
        assert.equal(
            outcome.message,
            "Draft is not valid Python: line 1: invalid syntax",
        );
    });
});

function createClient(
    fetchImpl: typeof fetch,
    now: () => number = Date.now,
    getNow?: () => number,
    timeoutMs = 5_000,
): TailEventsApiClient {
    return new TailEventsApiClient(
        () => "http://127.0.0.1:8766/api/v1/",
        () => timeoutMs,
        () => {
            return;
        },
        fetchImpl,
        getNow ?? now,
    );
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

function sampleTaskStep(): BackendTaskStepEvent {
    return {
        task_id: "task_1",
        step_id: "step_1",
        step_kind: "view",
        status: "succeeded",
        file_path: "pkg/demo.py",
        content_hash: "hash",
        intent: "Observe the target file before editing",
        reasoning_summary: null,
        tool_name: "view_file",
        input_summary: "request file view for pkg/demo.py",
        output_summary: "version=1, chars=9",
        timestamp: "2026-04-16T00:00:00Z",
    };
}

function sampleToolCall(): BackendToolCallPayload {
    return {
        task_id: "task_1",
        call_id: "call_1",
        step_id: "step_1",
        tool_name: "view_file",
        file_path: "pkg/demo.py",
        intent: "Observe the target file before editing",
    };
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

function jsonResponse(payload: unknown, status = 200): Response {
    return new Response(JSON.stringify(payload), {
        status,
        headers: {
            "Content-Type": "application/json",
        },
    });
}

function sseResponse(
    items: Array<{ event: string; data: unknown }>,
): Response {
    const body = items
        .map((item) => {
            return `event: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`;
        })
        .join("");
    return new Response(body, {
        status: 200,
        headers: {
            "Content-Type": "text/event-stream",
        },
    });
}

function parseJsonBody(body: BodyInit | null | undefined): unknown {
    if (typeof body !== "string") {
        return undefined;
    }
    return JSON.parse(body);
}
