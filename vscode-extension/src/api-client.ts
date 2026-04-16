import {
    type ApiErrorCategory,
    type ApiResult,
    type BackendCodeEntity,
    type BackendEntityExplanation,
    type BackendTailEvent,
    type CodingTaskRequestPayload,
    type CodingTaskResult,
    type CreateRawEventPayload,
} from "./types";

type FetchLike = typeof fetch;
type Logger = (message: string) => void;
type NowProvider = () => number;

const DEFAULT_CACHE_TTL_MS = 30_000;

interface CachedValue<T> {
    data: T;
    expires: number;
}

export interface TailEventsApi {
    getEntityByLocation(
        file: string,
        line: number,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendCodeEntity>>;
    getEntity(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendCodeEntity>>;
    getExplanationSummary(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendEntityExplanation>>;
    getExplanationFull(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendEntityExplanation>>;
    getEntityEvents(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendTailEvent[]>>;
    createEvent(
        payload: CreateRawEventPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendTailEvent>>;
    runCodingTaskStream(
        payload: CodingTaskRequestPayload,
        handlers: CodingTaskStreamHandlers,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskResult>>;
}

export interface CodingTaskStreamHandlers {
    onDelta?: (text: string) => void;
}

export class TailEventsApiClient implements TailEventsApi {
    private readonly entityCache = new Map<string, CachedValue<BackendCodeEntity>>();

    private readonly summaryCache = new Map<string, CachedValue<BackendEntityExplanation>>();

    public constructor(
        private readonly getBaseUrl: () => string,
        private readonly getTimeoutMs: () => number,
        private readonly log: Logger,
        private readonly fetchImpl: FetchLike = globalThis.fetch,
        private readonly now: NowProvider = Date.now,
    ) {}

    public async getEntityByLocation(
        file: string,
        line: number,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendCodeEntity>> {
        const cacheKey = `${file}:${line}`;
        const cached = this.getCached(this.entityCache, cacheKey);
        if (cached) {
            return success(cached, 200);
        }

        const result = await this.requestJson<BackendCodeEntity>(
            "/entities/by-location",
            {
                file,
                line: String(line),
            },
            signal,
        );
        if (result.ok) {
            this.setCached(this.entityCache, cacheKey, result.data);
        }
        return result;
    }

    public async getEntity(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendCodeEntity>> {
        return this.requestJson<BackendCodeEntity>(
            `/entities/${encodeURIComponent(entityId)}`,
            undefined,
            signal,
        );
    }

    public async getExplanationSummary(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendEntityExplanation>> {
        const cached = this.getCached(this.summaryCache, entityId);
        if (cached) {
            return success(cached, 200);
        }

        const result = await this.requestJson<BackendEntityExplanation>(
            `/explain/${encodeURIComponent(entityId)}/summary`,
            undefined,
            signal,
        );
        if (result.ok) {
            this.setCached(this.summaryCache, entityId, result.data);
        }
        return result;
    }

    public async getExplanationFull(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendEntityExplanation>> {
        return this.requestJson<BackendEntityExplanation>(
            `/explain/${encodeURIComponent(entityId)}`,
            undefined,
            signal,
        );
    }

    public async getEntityEvents(
        entityId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendTailEvent[]>> {
        return this.requestJson<BackendTailEvent[]>(
            `/events/for-entity/${encodeURIComponent(entityId)}`,
            undefined,
            signal,
        );
    }

    public async createEvent(
        payload: CreateRawEventPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<BackendTailEvent>> {
        return this.requestJson<BackendTailEvent>(
            "/events",
            undefined,
            signal,
            {
                method: "POST",
                body: payload,
            },
        );
    }

    public async runCodingTaskStream(
        payload: CodingTaskRequestPayload,
        handlers: CodingTaskStreamHandlers,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskResult>> {
        const url = this.buildUrl("/tasks/stream", undefined);
        const { signal: mergedSignal, cleanup } = this.buildMergedSignal(signal, false);
        try {
            const response = await this.fetchImpl(url, {
                headers: {
                    Accept: "text/event-stream",
                    "Content-Type": "application/json",
                },
                method: "POST",
                body: JSON.stringify(payload),
                signal: mergedSignal,
            });

            if (!response.ok) {
                return failure(classifyStatus(response.status), response.status);
            }
            if (!response.body) {
                return failure("unknown", response.status);
            }

            const parsed = await consumeSseStream(response.body, handlers);
            if (!parsed.ok) {
                return failure(parsed.error, response.status);
            }
            return success(parsed.result, response.status);
        } catch (error) {
            const category = classifyFetchError(error, mergedSignal, signal);
            if (!(signal?.aborted ?? false) && category !== "timeout") {
                this.log(`[TailEvents] Task stream failed for ${url}: ${formatUnknownError(error)}`);
            }
            return failure(category, null);
        } finally {
            cleanup();
        }
    }

    private async requestJson<T>(
        route: string,
        query: Record<string, string> | undefined,
        signal?: AbortSignal,
        init?: {
            method?: "GET" | "POST";
            body?: unknown;
        },
    ): Promise<ApiResult<T>> {
        const url = this.buildUrl(route, query);
        const { signal: mergedSignal, cleanup } = this.buildMergedSignal(signal);

        try {
            const body = init?.body === undefined ? undefined : JSON.stringify(init.body);
            const response = await this.fetchImpl(url, {
                headers: {
                    Accept: "application/json",
                    ...(body ? { "Content-Type": "application/json" } : {}),
                },
                method: init?.method ?? "GET",
                body,
                signal: mergedSignal,
            });

            if (!response.ok) {
                return failure(classifyStatus(response.status), response.status);
            }

            const payload = (await response.json()) as T;
            return success(payload, response.status);
        } catch (error) {
            const category = classifyFetchError(error, mergedSignal, signal);
            if (!(signal?.aborted ?? false) && category !== "timeout") {
                this.log(`[TailEvents] Request failed for ${url}: ${formatUnknownError(error)}`);
            }
            if (category === "timeout") {
                this.log(`[TailEvents] Request timed out for ${url}`);
            }
            return failure(category, null);
        } finally {
            cleanup();
        }
    }

    private buildUrl(route: string, query: Record<string, string> | undefined): string {
        const baseUrl = normalizeBaseUrl(this.getBaseUrl());
        const url = new URL(`${baseUrl}${route}`);
        for (const [key, value] of Object.entries(query ?? {})) {
            url.searchParams.set(key, value);
        }
        return url.toString();
    }

    private buildMergedSignal(
        signal: AbortSignal | undefined,
        useTimeout = true,
    ): { signal: AbortSignal; cleanup: () => void } {
        const timeoutSignal = useTimeout
            ? AbortSignal.timeout(Math.max(100, this.getTimeoutMs()))
            : null;
        if (!signal) {
            return {
                signal: timeoutSignal ?? new AbortController().signal,
                cleanup: () => {
                    return;
                },
            };
        }

        if (signal.aborted) {
            return {
                signal: AbortSignal.abort(signal.reason),
                cleanup: () => {
                    return;
                },
            };
        }

        const controller = new AbortController();
        const abortFrom = (source: AbortSignal) => {
            if (!controller.signal.aborted) {
                controller.abort(source.reason);
            }
        };

        const onTimeout = () => {
            if (timeoutSignal) {
                abortFrom(timeoutSignal);
            }
        };
        const onSignal = () => abortFrom(signal);
        timeoutSignal?.addEventListener("abort", onTimeout, { once: true });
        signal.addEventListener("abort", onSignal, { once: true });

        return {
            signal: controller.signal,
            cleanup: () => {
                timeoutSignal?.removeEventListener("abort", onTimeout);
                signal.removeEventListener("abort", onSignal);
            },
        };
    }

    private getCached<T>(cache: Map<string, CachedValue<T>>, key: string): T | null {
        const cached = cache.get(key);
        if (!cached) {
            return null;
        }
        if (cached.expires <= this.now()) {
            cache.delete(key);
            return null;
        }
        return cached.data;
    }

    private setCached<T>(cache: Map<string, CachedValue<T>>, key: string, data: T): void {
        cache.set(key, {
            data,
            expires: this.now() + DEFAULT_CACHE_TTL_MS,
        });
    }
}

function normalizeBaseUrl(baseUrl: string): string {
    return baseUrl.trim().replace(/\/+$/, "");
}

async function consumeSseStream(
    stream: ReadableStream<Uint8Array>,
    handlers: CodingTaskStreamHandlers,
): Promise<
    | { ok: true; result: CodingTaskResult }
    | { ok: false; error: ApiErrorCategory }
> {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: CodingTaskResult | null = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) {
            break;
        }
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
            const parsed = parseSseBlock(block);
            if (!parsed) {
                continue;
            }
            if (parsed.event === "delta" && typeof parsed.data?.text === "string") {
                handlers.onDelta?.(parsed.data.text);
                continue;
            }
            if (parsed.event === "result" && isCodingTaskResult(parsed.data)) {
                result = parsed.data;
                continue;
            }
            if (parsed.event === "error") {
                return { ok: false, error: "unknown" };
            }
            if (parsed.event === "done") {
                return result
                    ? { ok: true, result }
                    : { ok: false, error: "unknown" };
            }
        }
    }

    return result ? { ok: true, result } : { ok: false, error: "unknown" };
}

function parseSseBlock(block: string): { event: string; data: any } | null {
    let event = "";
    const dataLines: string[] = [];
    for (const rawLine of block.split(/\r?\n/)) {
        const line = rawLine.trimEnd();
        if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim();
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trim());
        }
    }
    if (!event || dataLines.length === 0) {
        return null;
    }
    try {
        return {
            event,
            data: JSON.parse(dataLines.join("\n")),
        };
    } catch {
        return null;
    }
}

function isCodingTaskResult(value: any): value is CodingTaskResult {
    return Boolean(
        value &&
        typeof value.updated_file_content === "string" &&
        value.updated_file_content.length > 0 &&
        typeof value.intent === "string" &&
        value.intent.trim().length > 0 &&
        (value.reasoning === null || value.reasoning === undefined || typeof value.reasoning === "string") &&
        (value.action_type === "create" || value.action_type === "modify"),
    );
}

function classifyStatus(status: number): ApiErrorCategory {
    if (status === 404) {
        return "entity_not_found";
    }
    if (status === 408 || status === 504) {
        return "timeout";
    }
    if (status === 502 || status === 503) {
        return "backend_unavailable";
    }
    return "unknown";
}

function classifyFetchError(
    error: unknown,
    signal: AbortSignal,
    callerSignal?: AbortSignal,
): ApiErrorCategory {
    if (isTimeoutAbort(signal.reason) || isTimeoutAbort(error)) {
        return "timeout";
    }
    if (callerSignal?.aborted) {
        return "unknown";
    }
    if (error instanceof TypeError) {
        return "backend_unavailable";
    }
    return "unknown";
}

function isTimeoutAbort(value: unknown): boolean {
    return value instanceof Error && value.name === "TimeoutError";
}

function formatUnknownError(error: unknown): string {
    if (error instanceof Error) {
        return `${error.name}: ${error.message}`;
    }
    return String(error);
}

function success<T>(data: T, status: number): ApiResult<T> {
    return {
        ok: true,
        data,
        status,
    };
}

function failure<T>(error: ApiErrorCategory, status: number | null): ApiResult<T> {
    return {
        ok: false,
        error,
        status,
    };
}
