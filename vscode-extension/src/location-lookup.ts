import type { TailEventsApi } from "./api-client";
import type { ApiResult, BackendCodeEntity } from "./types";

export async function findEntityByLocation(
    apiClient: TailEventsApi,
    fileCandidates: readonly string[],
    line: number,
    signal?: AbortSignal,
): Promise<{ file: string; result: ApiResult<BackendCodeEntity> }> {
    let lastResult: ApiResult<BackendCodeEntity> = {
        ok: false,
        error: "entity_not_found",
        status: 404,
    };

    for (const file of fileCandidates) {
        const result = await apiClient.getEntityByLocation(file, line, signal);
        if (result.ok) {
            return { file, result };
        }
        lastResult = result;
        if (result.error !== "entity_not_found") {
            return { file, result };
        }
    }

    return {
        file: fileCandidates[0] ?? "",
        result: lastResult,
    };
}
