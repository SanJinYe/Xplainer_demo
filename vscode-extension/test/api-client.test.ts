import { strict as assert } from "node:assert";

import { TailEventsApiClient } from "../src/api-client";
import type { BackendCodeEntity, BackendEntityExplanation } from "../src/types";

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

    it("posts RawEvent payloads to the events endpoint", async () => {
        let receivedMethod = "";
        let receivedBody = "";
        const client = createClient(async (_url, init) => {
            receivedMethod = String(init?.method);
            receivedBody = String(init?.body);
            return jsonResponse({
                event_id: "te_1",
                timestamp: "2026-04-15T00:00:00Z",
                agent_step_id: null,
                session_id: "b0_123456abcdef",
                action_type: "modify",
                file_path: "pkg/demo.py",
                line_range: null,
                code_snapshot: "print(1)\n",
                intent: "change output to 1",
                reasoning: null,
                decision_alternatives: null,
                entity_refs: [],
                external_refs: [],
            });
        });

        const result = await client.createEvent({
            action_type: "modify",
            file_path: "pkg/demo.py",
            code_snapshot: "print(1)\n",
            intent: "change output to 1",
            reasoning: null,
            decision_alternatives: null,
            session_id: "b0_123456abcdef",
            line_range: null,
            external_refs: [],
        });

        assert.equal(result.ok, true);
        assert.equal(receivedMethod, "POST");
        assert.ok(receivedBody.includes('"file_path":"pkg/demo.py"'));
    });

    it("parses task stream SSE responses", async () => {
        let requestedAccept = "";
        const client = createClient(async (_url, init) => {
            requestedAccept = String((init?.headers as Record<string, string>)?.Accept);
            return sseResponse([
                'event: delta\ndata: {"text":"hello "}\n\n',
                'event: delta\ndata: {"text":"world"}\n\n',
                'event: result\ndata: {"updated_file_content":"print(1)\\n","edits":[{"old_text":"print(0)\\n","new_text":"print(1)\\n"}],"intent":"change output","reasoning":null,"action_type":"modify"}\n\n',
                'event: done\ndata: {}\n\n',
            ]);
        });

        let streamed = "";
        const result = await client.runCodingTaskStream(
            {
                file_path: "pkg/demo.py",
                file_content: "print(0)\n",
                user_prompt: "change output to 1",
            },
            {
                onDelta(text) {
                    streamed += text;
                },
            },
        );

        assert.equal(requestedAccept, "text/event-stream");
        assert.equal(streamed, "hello world");
        assert.equal(result.ok, true);
        assert.equal(result.ok ? result.data.action_type : "", "modify");
        assert.deepEqual(
            result.ok ? result.data.edits : [],
            [{ old_text: "print(0)\n", new_text: "print(1)\n" }],
        );
    });

    it("preserves task stream error messages from SSE error events", async () => {
        const client = createClient(async () => {
            return sseResponse([
                'event: delta\ndata: {"text":"broken output"}\n\n',
                'event: error\ndata: {"message":"updated_file_content is not valid Python: line 1: invalid syntax"}\n\n',
                'event: done\ndata: {}\n\n',
            ]);
        });

        const result = await client.runCodingTaskStream(
            {
                file_path: "pkg/demo.py",
                file_content: "print(0)\n",
                user_prompt: "change output to 1",
            },
            {},
        );

        assert.equal(result.ok, false);
        assert.equal(result.ok ? "" : result.error, "unknown");
        assert.equal(
            result.ok ? "" : result.message,
            "updated_file_content is not valid Python: line 1: invalid syntax",
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

function jsonResponse(payload: unknown): Response {
    return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
            "Content-Type": "application/json",
        },
    });
}

function sseResponse(chunks: string[]): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            for (const chunk of chunks) {
                controller.enqueue(encoder.encode(chunk));
            }
            controller.close();
        },
    });
    return new Response(stream, {
        status: 200,
        headers: {
            "Content-Type": "text/event-stream",
        },
    });
}
