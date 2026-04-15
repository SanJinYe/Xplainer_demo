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
