import {
    type ApiErrorCategory,
    type ApiResult,
    type BaselineOnboardFilePayload,
    type BaselineOnboardFileResult,
    type BackendCodeEntity,
    type BackendEntityExplanation,
    type BackendTailEvent,
    type BackendTaskStepEvent,
    type BackendToolCallPayload,
    type CodingTaskCreateRequestPayload,
    type CodingTaskCreateResponse,
    type CodingTaskDraftResult,
    type CodingTaskToolResultPayload,
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

export interface CodingTaskSessionHandlers {
    onCreated?: (taskId: string) => void;
    onStatus?: (status: string) => void;
    onStep?: (step: BackendTaskStepEvent) => void;
    onModelDelta?: (text: string) => void;
    onToolCall?: (payload: BackendToolCallPayload) => Promise<CodingTaskToolResultPayload>;
    onResult?: (result: CodingTaskDraftResult) => void;
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
    onboardBaselineFile(
        payload: BaselineOnboardFilePayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<BaselineOnboardFileResult>>;
    createCodingTask(
        payload: CodingTaskCreateRequestPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskCreateResponse>>;
    submitCodingToolResult(
        taskId: string,
        payload: CodingTaskToolResultPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<null>>;
    cancelCodingTask(
        taskId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<null>>;
    runCodingTaskSession(
        payload: CodingTaskCreateRequestPayload,
        handlers: CodingTaskSessionHandlers,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskDraftResult>>;
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

    public async onboardBaselineFile(
        payload: BaselineOnboardFilePayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<BaselineOnboardFileResult>> {
        return this.requestJson<BaselineOnboardFileResult>(
            "/baseline/onboard-file",
            undefined,
            signal,
            {
                method: "POST",
                body: payload,
            },
        );
    }

    public async createCodingTask(
        payload: CodingTaskCreateRequestPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskCreateResponse>> {
        return this.requestJson<CodingTaskCreateResponse>(
            "/coding/tasks",
            undefined,
            signal,
            {
                method: "POST",
                body: payload,
            },
        );
    }

    public async submitCodingToolResult(
        taskId: string,
        payload: CodingTaskToolResultPayload,
        signal?: AbortSignal,
    ): Promise<ApiResult<null>> {
        return this.requestNoContent(
            `/coding/tasks/${encodeURIComponent(taskId)}/tool-result`,
            signal,
            {
                method: "POST",
                body: payload,
            },
        );
    }

    public async cancelCodingTask(
        taskId: string,
        signal?: AbortSignal,
    ): Promise<ApiResult<null>> {
        return this.requestNoContent(
            `/coding/tasks/${encodeURIComponent(taskId)}/cancel`,
            signal,
            {
                method: "POST",
            },
        );
    }

    public async runCodingTaskSession(
        payload: CodingTaskCreateRequestPayload,
        handlers: CodingTaskSessionHandlers,
        signal?: AbortSignal,
    ): Promise<ApiResult<CodingTaskDraftResult>> {
        const created = await this.createCodingTask(payload, signal);
        if (!created.ok) {
            return failure(created.error, created.status, created.message);
        }

        handlers.onCreated?.(created.data.task_id);

        const url = this.buildUrl(
            `/coding/tasks/${encodeURIComponent(created.data.task_id)}/stream`,
            undefined,
        );
        const { signal: mergedSignal, cleanup } = this.buildMergedSignal(signal, false);

        try {
            const response = await this.fetchImpl(url, {
                headers: {
                    Accept: "text/event-stream",
                },
                method: "GET",
                signal: mergedSignal,
            });

            if (!response.ok) {
                return failure(classifyStatus(response.status), response.status);
            }
            if (!response.body) {
                return failure("unknown", response.status);
            }

            const parsed = await this.consumeCodingTaskStream(
                created.data.task_id,
                response.body,
                handlers,
                mergedSignal,
            );
            if (!parsed.ok) {
                return failure(parsed.error, response.status, parsed.message);
            }
            return success(parsed.result, response.status);
        } catch (error) {
            const category = classifyFetchError(error, mergedSignal, signal);
            if (!(signal?.aborted ?? false) && category !== "timeout") {
                this.log(`[TailEvents] Coding task stream failed for ${url}: ${formatUnknownError(error)}`);
            }
            return failure(category, null);
        } finally {
            cleanup();
        }
    }

    private async consumeCodingTaskStream(
        taskId: string,
        stream: ReadableStream<Uint8Array>,
        handlers: CodingTaskSessionHandlers,
        signal: AbortSignal,
    ): Promise<
        | { ok: true; result: CodingTaskDraftResult }
        | { ok: false; error: ApiErrorCategory; message?: string }
    > {
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let result: CodingTaskDraftResult | null = null;

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

                if (parsed.event === "status" && typeof parsed.data?.status === "string") {
                    handlers.onStatus?.(parsed.data.status);
                    continue;
                }

                if (parsed.event === "step" && isTaskStepEvent(parsed.data)) {
                    handlers.onStep?.(parsed.data);
                    continue;
                }

                if (parsed.event === "model_delta" && typeof parsed.data?.text === "string") {
                    handlers.onModelDelta?.(parsed.data.text);
                    continue;
                }

                if (parsed.event === "tool_call" && isToolCallPayload(parsed.data)) {
                    if (!handlers.onToolCall) {
                        return {
                            ok: false,
                            error: "unknown",
                            message: "No local tool handler is registered for tool_call events.",
                        };
                    }
                    const toolResult = await handlers.onToolCall(parsed.data);
                    const submitResult = await this.submitCodingToolResult(taskId, toolResult, signal);
                    if (!submitResult.ok) {
                        return {
                            ok: false,
                            error: submitResult.error,
                            message: submitResult.message,
                        };
                    }
                    continue;
                }

                if (parsed.event === "result" && isCodingTaskDraftResult(parsed.data)) {
                    result = parsed.data;
                    handlers.onResult?.(parsed.data);
                    continue;
                }

                if (parsed.event === "error") {
                    return {
                        ok: false,
                        error: "unknown",
                        message: extractErrorMessage(parsed.data),
                    };
                }

                if (parsed.event === "done") {
                    if (result) {
                        return { ok: true, result };
                    }
                    return {
                        ok: false,
                        error: "unknown",
                        message: "Task finished without a verified draft.",
                    };
                }
            }
        }

        return {
            ok: false,
            error: "unknown",
            message: "Task stream ended before a done event was received.",
        };
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

    private async requestNoContent(
        route: string,
        signal?: AbortSignal,
        init?: {
            method?: "POST";
            body?: unknown;
        },
    ): Promise<ApiResult<null>> {
        const url = this.buildUrl(route, undefined);
        const { signal: mergedSignal, cleanup } = this.buildMergedSignal(signal);

        try {
            const body = init?.body === undefined ? undefined : JSON.stringify(init.body);
            const response = await this.fetchImpl(url, {
                headers: {
                    Accept: "application/json",
                    ...(body ? { "Content-Type": "application/json" } : {}),
                },
                method: init?.method ?? "POST",
                body,
                signal: mergedSignal,
            });

            if (!response.ok) {
                return failure(classifyStatus(response.status), response.status);
            }
            return success(null, response.status);
        } catch (error) {
            const category = classifyFetchError(error, mergedSignal, signal);
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

function parseSseBlock(block: string): { event: string; data: any } | null {
    const lines = block.split(/\r?\n/);
    let event = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
        if (line.startsWith("event:")) {
            event = line.slice(6).trim();
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
        }
    }

    if (dataLines.length === 0) {
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

function isTaskStepEvent(value: any): value is BackendTaskStepEvent {
    return Boolean(
        value &&
        typeof value.task_id === "string" &&
        typeof value.step_id === "string" &&
        typeof value.step_kind === "string" &&
        typeof value.status === "string" &&
        typeof value.file_path === "string" &&
        typeof value.intent === "string" &&
        typeof value.timestamp === "string",
    );
}

function isToolCallPayload(value: any): value is BackendToolCallPayload {
    return Boolean(
        value &&
        typeof value.task_id === "string" &&
        typeof value.call_id === "string" &&
        typeof value.step_id === "string" &&
        value.tool_name === "view_file" &&
        typeof value.file_path === "string" &&
        typeof value.intent === "string",
    );
}

function isCodingTaskDraftResult(value: any): value is CodingTaskDraftResult {
    return Boolean(
        value &&
        typeof value.task_id === "string" &&
        typeof value.updated_file_content === "string" &&
        value.updated_file_content.length > 0 &&
        typeof value.intent === "string" &&
        value.intent.trim().length > 0 &&
        (value.reasoning === null || value.reasoning === undefined || typeof value.reasoning === "string") &&
        typeof value.session_id === "string" &&
        typeof value.agent_step_id === "string" &&
        value.action_type === "modify",
    );
}

function extractErrorMessage(value: any): string | undefined {
    if (!value || typeof value.message !== "string") {
        return undefined;
    }
    const message = value.message.trim();
    return message.length > 0 ? message : undefined;
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
        return error.message;
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

function failure<T>(
    error: ApiErrorCategory,
    status: number | null,
    message?: string,
): ApiResult<T> {
    return {
        ok: false,
        error,
        status,
        ...(message ? { message } : {}),
    };
}
