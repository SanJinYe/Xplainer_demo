import path from "node:path";

import type { TailEventsApi } from "./api-client";

export const MAX_ONBOARD_FILE_BYTES = 512 * 1024;

const EXCLUDED_DIRECTORY_SEGMENTS = new Set([
    "node_modules",
    "__pycache__",
    "out",
    "dist",
    "build",
]);

type LocalSkipReason = "file_too_large" | "unreadable_encoding";
type ProgressCallback = (current: number, total: number, workspaceFilePath: string) => void;

export interface OnboardingCandidate {
    absolutePath: string;
    workspaceFilePath: string;
}

export interface OnboardingSummary {
    total: number;
    created: number;
    skipped: number;
    failed: number;
    cancelled: boolean;
}

interface OnboardWorkspaceFilesOptions {
    apiClient: Pick<TailEventsApi, "onboardBaselineFile">;
    candidates: OnboardingCandidate[];
    readFileBytes: (absolutePath: string) => Promise<Uint8Array>;
    isCancellationRequested: () => boolean;
    log: (message: string) => void;
    onProgress?: ProgressCallback;
}

export function shouldIncludeOnboardingPath(workspaceFilePath: string): boolean {
    const normalized = workspaceFilePath.replace(/\\/g, "/");
    if (!normalized.toLowerCase().endsWith(".py")) {
        return false;
    }

    const segments = normalized.split("/").filter((segment) => segment.length > 0);
    return segments.every((segment) => {
        if (segment.startsWith(".")) {
            return false;
        }
        if (EXCLUDED_DIRECTORY_SEGMENTS.has(segment)) {
            return false;
        }
        if (segment.endsWith(".egg-info")) {
            return false;
        }
        return true;
    });
}

export function buildOnboardingCandidates(
    workspaceRootPath: string,
    absolutePaths: string[],
): OnboardingCandidate[] {
    const deduped = new Map<string, OnboardingCandidate>();
    for (const absolutePath of absolutePaths) {
        const relativePath = path.relative(workspaceRootPath, absolutePath);
        if (!relativePath || relativePath.startsWith("..") || path.isAbsolute(relativePath)) {
            continue;
        }

        const workspaceFilePath = relativePath.replace(/\\/g, "/");
        if (!shouldIncludeOnboardingPath(workspaceFilePath)) {
            continue;
        }

        deduped.set(absolutePath, {
            absolutePath,
            workspaceFilePath,
        });
    }

    return Array.from(deduped.values()).sort((left, right) => {
        const byWorkspacePath = left.workspaceFilePath.localeCompare(right.workspaceFilePath);
        if (byWorkspacePath !== 0) {
            return byWorkspacePath;
        }
        return left.absolutePath.localeCompare(right.absolutePath);
    });
}

export async function onboardWorkspaceFiles(
    options: OnboardWorkspaceFilesOptions,
): Promise<OnboardingSummary> {
    const summary: OnboardingSummary = {
        total: options.candidates.length,
        created: 0,
        skipped: 0,
        failed: 0,
        cancelled: false,
    };

    for (let index = 0; index < options.candidates.length; index += 1) {
        const candidate = options.candidates[index];
        if (options.isCancellationRequested()) {
            summary.cancelled = true;
            break;
        }

        options.onProgress?.(index + 1, options.candidates.length, candidate.workspaceFilePath);

        let fileBytes: Uint8Array;
        try {
            fileBytes = await options.readFileBytes(candidate.absolutePath);
        } catch (error) {
            summary.failed += 1;
            options.log(
                `[TailEvents] Onboard failed (read_error): ${candidate.workspaceFilePath} - ${formatUnknownError(error)}`,
            );
            continue;
        }

        if (fileBytes.byteLength > MAX_ONBOARD_FILE_BYTES) {
            summary.skipped += 1;
            options.log(
                `[TailEvents] Onboard skipped (file_too_large): ${candidate.workspaceFilePath}`,
            );
            continue;
        }

        let codeSnapshot: string;
        try {
            codeSnapshot = decodeUtf8Strict(fileBytes);
        } catch {
            summary.skipped += 1;
            options.log(
                `[TailEvents] Onboard skipped (unreadable_encoding): ${candidate.workspaceFilePath}`,
            );
            continue;
        }

        const response = await options.apiClient.onboardBaselineFile({
            file_path: candidate.workspaceFilePath,
            code_snapshot: codeSnapshot,
        });
        if (!response.ok) {
            summary.failed += 1;
            options.log(
                `[TailEvents] Onboard failed (${response.error}): ${candidate.workspaceFilePath}`,
            );
            continue;
        }

        if (response.data.status === "created") {
            summary.created += 1;
            options.log(`[TailEvents] Onboard created: ${candidate.workspaceFilePath}`);
            continue;
        }

        summary.skipped += 1;
        options.log(
            `[TailEvents] Onboard skipped (${response.data.reason ?? "unknown"}): ${candidate.workspaceFilePath}`,
        );
    }

    return summary;
}

export function formatOnboardingSummary(summary: OnboardingSummary): string {
    const prefix = summary.cancelled
        ? "TailEvents onboarding cancelled"
        : "TailEvents onboarding finished";
    return `${prefix}: ${summary.created} created, ${summary.skipped} skipped, ${summary.failed} failed.`;
}

function decodeUtf8Strict(fileBytes: Uint8Array): string {
    return new TextDecoder("utf-8", { fatal: true }).decode(fileBytes);
}

function formatUnknownError(error: unknown): string {
    if (error instanceof Error && error.message) {
        return error.message;
    }
    return String(error);
}

export type { LocalSkipReason };
