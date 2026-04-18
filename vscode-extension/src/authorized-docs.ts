export interface AuthorizedDocCandidate {
    workspaceFilePath: string;
    absolutePath: string;
}

export interface AuthorizedDocQuickPickItem {
    label: string;
    description?: string;
    detail?: string;
    picked: boolean;
}

export function isWorkspaceRootReadme(workspaceFilePath: string): boolean {
    return workspaceFilePath.trim().toLowerCase() === "readme.md";
}

export function compareAuthorizedDocCandidates(
    left: AuthorizedDocCandidate,
    right: AuthorizedDocCandidate,
): number {
    const leftIsRootReadme = isWorkspaceRootReadme(left.workspaceFilePath);
    const rightIsRootReadme = isWorkspaceRootReadme(right.workspaceFilePath);
    if (leftIsRootReadme !== rightIsRootReadme) {
        return leftIsRootReadme ? -1 : 1;
    }

    const byWorkspacePath = left.workspaceFilePath.localeCompare(right.workspaceFilePath);
    if (byWorkspacePath !== 0) {
        return byWorkspacePath;
    }
    return left.absolutePath.localeCompare(right.absolutePath);
}

export function buildAuthorizedDocQuickPickItems(
    candidates: AuthorizedDocCandidate[],
    selectedPaths: string[],
): AuthorizedDocQuickPickItem[] {
    return [...candidates]
        .sort(compareAuthorizedDocCandidates)
        .map((item) => {
            const isRootReadme = isWorkspaceRootReadme(item.workspaceFilePath);
            return {
                label: item.workspaceFilePath,
                description: isRootReadme ? "Recommended" : undefined,
                detail: isRootReadme ? "workspace root" : undefined,
                picked: selectedPaths.includes(item.workspaceFilePath),
            };
        });
}
