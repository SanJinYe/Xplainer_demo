import path from "node:path";

import type { WorkspaceFolder } from "vscode";

export function toWorkspaceRelativePath(
    absolutePath: string,
    workspaceFolders: readonly WorkspaceFolder[] | undefined,
): string | null {
    const candidates = getWorkspaceRelativePathCandidates(absolutePath, workspaceFolders);
    return candidates.length > 0 ? candidates[0] : null;
}

export function getWorkspaceRelativePathCandidates(
    absolutePath: string,
    workspaceFolders: readonly WorkspaceFolder[] | undefined,
): string[] {
    if (!absolutePath || !path.isAbsolute(absolutePath)) {
        return [];
    }
    if (!workspaceFolders || workspaceFolders.length === 0) {
        return [];
    }

    const targetPath = path.resolve(absolutePath);
    const normalizedTarget = normalizeForComparison(targetPath);

    const candidates = workspaceFolders
        .map((folder) => {
            const rootPath = path.resolve(folder.uri.fsPath);
            const relativePath = path.relative(rootPath, targetPath);

            if (relativePath === "") {
                return null;
            }
            if (
                relativePath.startsWith("..") ||
                path.isAbsolute(relativePath) ||
                normalizedTarget === normalizeForComparison(rootPath)
            ) {
                return null;
            }

            return {
                rootPath,
                relativePath,
            };
        })
        .filter((candidate): candidate is { rootPath: string; relativePath: string } => {
            return candidate !== null;
        })
        .sort((left, right) => right.rootPath.length - left.rootPath.length);

    if (candidates.length === 0) {
        return [];
    }

    const values: string[] = [];
    for (const candidate of candidates) {
        const relativePath = candidate.relativePath.replace(/\\/g, "/");
        pushUnique(values, relativePath);

        const folderName = path.basename(candidate.rootPath).replace(/\\/g, "/");
        if (folderName.length > 0) {
            pushUnique(values, `${folderName}/${relativePath}`);
        }
    }
    return values;
}

export function getFileLookupCandidates(
    absolutePath: string,
    workspaceFolders: readonly WorkspaceFolder[] | undefined,
): string[] {
    const values = getWorkspaceRelativePathCandidates(absolutePath, workspaceFolders);
    for (const candidate of getPathSuffixCandidates(absolutePath)) {
        pushUnique(values, candidate);
    }
    return values;
}

function normalizeForComparison(value: string): string {
    const normalized = path.normalize(value);
    return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function getPathSuffixCandidates(absolutePath: string): string[] {
    if (!absolutePath || !path.isAbsolute(absolutePath)) {
        return [];
    }

    const normalizedPath = path.normalize(path.resolve(absolutePath));
    const parsed = path.parse(normalizedPath);
    const relativeFromRoot = path.relative(parsed.root, normalizedPath);
    if (!relativeFromRoot) {
        return [];
    }

    const segments = relativeFromRoot
        .split(path.sep)
        .map((segment) => segment.trim())
        .filter((segment) => segment.length > 0);

    const values: string[] = [];
    for (let index = segments.length - 1; index >= 0; index -= 1) {
        pushUnique(values, segments.slice(index).join("/"));
    }
    return values;
}

function pushUnique(values: string[], candidate: string): void {
    if (!candidate || values.includes(candidate)) {
        return;
    }
    values.push(candidate);
}
