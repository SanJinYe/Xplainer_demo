import type {
    BackendTaskStepEvent,
    BackendVerifiedFileDraft,
    DraftFileViewModel,
    HistoryDetailStepViewModel,
} from "../types";

export function isSafeWorkspaceFilePath(workspaceFilePath: string): boolean {
    const normalized = workspaceFilePath.replace(/\\/g, "/").trim();
    if (!normalized) {
        return false;
    }
    if (normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized)) {
        return false;
    }
    const segments = normalized.split("/");
    return segments.every((segment) => segment.length > 0 && segment !== "." && segment !== "..");
}

export async function buildActiveDraftFileViewModels(
    verifiedFiles: readonly BackendVerifiedFileDraft[],
    loadBaseContent: (workspaceFilePath: string) => Promise<string | null>,
): Promise<DraftFileViewModel[]> {
    const items: DraftFileViewModel[] = [];
    for (const item of verifiedFiles) {
        const baseContent = await loadBaseContent(item.file_path);
        items.push({
            filePath: item.file_path,
            content: item.content,
            contentHash: item.content_hash,
            baseContent,
            baseSource: baseContent === null ? "unavailable" : "workspace_live",
            originalContentHash: item.original_content_hash ?? null,
            originalDocumentVersion: item.original_document_version ?? null,
        });
    }
    return items;
}

export function buildHistoryDraftFileViewModels(
    verifiedFiles: readonly BackendVerifiedFileDraft[],
): DraftFileViewModel[] {
    return verifiedFiles.map((item) => {
        return {
            filePath: item.file_path,
            content: item.content,
            contentHash: item.content_hash,
            baseContent: null,
            baseSource: "unavailable",
            originalContentHash: item.original_content_hash ?? null,
            originalDocumentVersion: item.original_document_version ?? null,
        };
    });
}

export function buildHistoryDetailStepViewModels(
    steps: readonly BackendTaskStepEvent[],
): HistoryDetailStepViewModel[] {
    return steps.map((step) => {
        return {
            stepId: step.step_id,
            stepKind: step.step_kind,
            status: step.status,
            filePath: step.file_path,
            summary: formatHistoryStepSummary(step),
            toolName: step.tool_name ?? null,
            timestamp: step.timestamp,
        };
    });
}

export function formatHistoryStepSummary(step: BackendTaskStepEvent): string {
    return step.output_summary || step.reasoning_summary || step.input_summary || step.intent;
}
