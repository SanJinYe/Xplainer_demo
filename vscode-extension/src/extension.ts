import path from "node:path";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";

import * as vscode from "vscode";

import { TailEventsApiClient } from "./api-client";
import { TailEventsHoverProvider } from "./hover-provider";
import { findEntityByLocation } from "./location-lookup";
import {
    buildOnboardingCandidates,
    formatOnboardingSummary,
    onboardWorkspaceFiles,
} from "./onboarding";
import { getFileLookupCandidates } from "./path-utils";
import { CodingProfileManager } from "./profile-manager";
import { TailEventsSidebarProvider } from "./sidebar-provider";
import type { BackendCodeEntity, ExplainCommandArgs } from "./types";

const COMMAND_EXPLAIN_CURRENT_SYMBOL = "tailEvents.explainCurrentSymbol";
const COMMAND_ONBOARD_REPOSITORY = "tailEvents.onboardRepository";
const COMMAND_OPEN_PANEL = "tailEvents.openPanel";
const COMMAND_REFRESH_PANEL = "tailEvents.refreshPanel";
const COMMAND_MANAGE_CODING_PROFILES = "tailEvents.manageCodingProfiles";
const VIEW_CONTAINER_ID = "tailevents-sidebar";
const VIEW_ID = "tailevents.sidebarView";
const DEFAULT_BASE_URL = "http://127.0.0.1:8766/api/v1";
const DEFAULT_TIMEOUT_MS = 5000;
const NO_INDEXED_ENTITY_MESSAGE = "No indexed entity at cursor position.";
const NO_WORKSPACE_MESSAGE = "Open a workspace folder before running TailEvents onboarding.";
const NO_ONBOARDING_FILES_MESSAGE = "No candidate Python files found for TailEvents onboarding.";

export function activate(context: vscode.ExtensionContext): void {
    const outputChannel = vscode.window.createOutputChannel("TailEvents");
    context.subscriptions.push(outputChannel);

    const getConfiguration = () => vscode.workspace.getConfiguration("tailEvents");
    const getBaseUrl = () => getConfiguration().get<string>("baseUrl", DEFAULT_BASE_URL);
    const getTimeoutMs = () => getConfiguration().get<number>("requestTimeoutMs", DEFAULT_TIMEOUT_MS);
    const isHoverEnabled = () => getConfiguration().get<boolean>("enableHoverPreview", true);

    const apiClient = new TailEventsApiClient(
        getBaseUrl,
        getTimeoutMs,
        (message) => outputChannel.appendLine(message),
    );
    const profileManager = new CodingProfileManager(context, apiClient);
    void profileManager.syncToBackend();

    const hoverProvider = new TailEventsHoverProvider({
        apiClient,
        getWorkspaceFolders: () => vscode.workspace.workspaceFolders,
        isHoverEnabled,
        vscodeApi: {
            Hover: vscode.Hover,
            MarkdownString: vscode.MarkdownString,
        },
        commandId: COMMAND_EXPLAIN_CURRENT_SYMBOL,
    });

    const sidebarProvider = new TailEventsSidebarProvider({
        apiClient,
        getBaseUrl,
        getSelectedProfileId: () => profileManager.getSelectedProfileId(),
        templatePath: path.join(context.extensionPath, "media", "sidebar.html"),
        runtime: {
            getActiveEditor: () => vscode.window.activeTextEditor ?? null,
            getWorkspaceFolders: () => vscode.workspace.workspaceFolders,
            resolveWorkspaceRelativePath: (absolutePath) => {
                const uri = vscode.Uri.file(absolutePath);
                const workspaceFolder = vscode.workspace.getWorkspaceFolder(uri);
                if (!workspaceFolder) {
                    return null;
                }
                const relativePath = path.relative(workspaceFolder.uri.fsPath, absolutePath);
                if (
                    !relativePath ||
                    relativePath.startsWith("..") ||
                    path.isAbsolute(relativePath)
                ) {
                    return null;
                }
                return relativePath.replace(/\\/g, "/");
            },
            resolveAbsoluteWorkspacePath: (workspaceFilePath) => {
                const folders = vscode.workspace.workspaceFolders ?? [];
                for (const folder of folders) {
                    const candidate = path.join(folder.uri.fsPath, workspaceFilePath);
                    if (existsSync(candidate)) {
                        return candidate;
                    }
                }
                return null;
            },
            listWorkspacePythonFiles: async () => {
                const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
                return collectWorkspacePythonCandidates(workspaceFolders);
            },
            getOpenDocumentByAbsolutePath: (absolutePath) => {
                return vscode.workspace.textDocuments.find((document) => {
                    return document.uri.scheme === "file" && document.uri.fsPath === absolutePath;
                }) ?? null;
            },
            openWorkspaceDocument: async (workspaceFilePath) => {
                const folders = vscode.workspace.workspaceFolders ?? [];
                for (const folder of folders) {
                    const candidate = path.join(folder.uri.fsPath, workspaceFilePath);
                    if (!existsSync(candidate)) {
                        continue;
                    }
                    return vscode.workspace.openTextDocument(vscode.Uri.file(candidate));
                }
                return null;
            },
            readFileText: async (absolutePath) => {
                const buffer = await readFile(absolutePath);
                return buffer.toString("utf8");
            },
            applyVerifiedFiles: async (files) => {
                const documents = await Promise.all(
                    files.map((item) => vscode.workspace.openTextDocument(vscode.Uri.file(item.absolutePath))),
                );
                const edit = new vscode.WorkspaceEdit();
                for (let index = 0; index < files.length; index += 1) {
                    const document = documents[index];
                    const lastLineIndex = Math.max(document.lineCount - 1, 0);
                    const lastLine = document.lineAt(lastLineIndex);
                    const range = new vscode.Range(
                        new vscode.Position(0, 0),
                        new vscode.Position(lastLineIndex, lastLine.text.length),
                    );
                    edit.replace(document.uri, range, files[index].content);
                }
                const applied = await vscode.workspace.applyEdit(edit);
                if (!applied) {
                    return false;
                }
                for (const document of documents) {
                    const saved = await document.save();
                    if (!saved) {
                        return false;
                    }
                }
                return true;
            },
            openWorkspaceFile: async (workspaceFilePath) => {
                const folders = vscode.workspace.workspaceFolders ?? [];
                for (const folder of folders) {
                    const candidate = path.join(folder.uri.fsPath, workspaceFilePath);
                    if (!existsSync(candidate)) {
                        continue;
                    }
                    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(candidate));
                    await vscode.window.showTextDocument(document, { preview: false });
                    return true;
                }
                return false;
            },
        },
    });

    context.subscriptions.push(
        vscode.languages.registerHoverProvider(
            {
                language: "python",
                scheme: "file",
            },
            hoverProvider,
        ),
    );

    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(VIEW_ID, sidebarProvider),
    );

    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(() => {
            void sidebarProvider.refreshCodeContext();
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand(
            COMMAND_EXPLAIN_CURRENT_SYMBOL,
            async (args?: ExplainCommandArgs) => {
                if (args?.entityId) {
                    await revealSidebar();
                    await sidebarProvider.showExplainEntity(args.entityId);
                    return;
                }

                const entity = await resolveEntityAtActiveLocation(args);
                if (!entity) {
                    return;
                }

                await revealSidebar();
                await sidebarProvider.showExplainEntity(entity.entity_id);
            },
        ),
        vscode.commands.registerCommand(COMMAND_ONBOARD_REPOSITORY, async () => {
            const workspaceFolders = vscode.workspace.workspaceFolders ?? [];
            if (workspaceFolders.length === 0) {
                vscode.window.showInformationMessage(NO_WORKSPACE_MESSAGE);
                return;
            }

            const candidates = await collectOnboardingCandidates(workspaceFolders);
            if (candidates.length === 0) {
                vscode.window.showInformationMessage(NO_ONBOARDING_FILES_MESSAGE);
                return;
            }

            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: "TailEvents: Onboard Repository",
                    cancellable: true,
                },
                async (progress, token) => {
                    outputChannel.appendLine(
                        `[TailEvents] Starting repository onboarding for ${candidates.length} file(s).`,
                    );
                    const summary = await onboardWorkspaceFiles({
                        apiClient,
                        candidates,
                        readFileBytes: async (absolutePath) => {
                            const buffer = await readFile(absolutePath);
                            return new Uint8Array(buffer);
                        },
                        isCancellationRequested: () => token.isCancellationRequested,
                        log: (message) => outputChannel.appendLine(message),
                        onProgress: (current, total, workspaceFilePath) => {
                            progress.report({
                                increment: 100 / Math.max(total, 1),
                                message: `${current}/${total}: ${workspaceFilePath}`,
                            });
                        },
                    });
                    apiClient.clearSummaryCache();
                    const message = formatOnboardingSummary(summary);
                    if (summary.cancelled) {
                        vscode.window.showWarningMessage(message);
                        return;
                    }
                    vscode.window.showInformationMessage(message);
                },
            );
        }),
        vscode.commands.registerCommand(COMMAND_OPEN_PANEL, async () => {
            await revealSidebar();
        }),
        vscode.commands.registerCommand(COMMAND_REFRESH_PANEL, async () => {
            const currentEntityId = sidebarProvider.getCurrentEntityId();
            if (currentEntityId) {
                await sidebarProvider.showExplainEntity(currentEntityId);
                return;
            }

            const entity = await resolveEntityAtActiveLocation();
            if (!entity) {
                return;
            }

            await revealSidebar();
            await sidebarProvider.showExplainEntity(entity.entity_id);
        }),
        vscode.commands.registerCommand(COMMAND_MANAGE_CODING_PROFILES, async () => {
            await profileManager.showManageProfilesQuickPick();
            await profileManager.syncToBackend();
        }),
    );

    async function resolveEntityAtActiveLocation(
        args?: ExplainCommandArgs,
    ): Promise<BackendCodeEntity | null> {
        if (args?.file && args.line) {
            const result = await apiClient.getEntityByLocation(args.file, args.line);
            return handleEntityLookupResult(result, false);
        }

        const editor = vscode.window.activeTextEditor;
        if (!editor || editor.document.isUntitled || editor.document.uri.scheme !== "file") {
            vscode.window.showInformationMessage(NO_INDEXED_ENTITY_MESSAGE);
            return null;
        }

        const fileCandidates = getFileLookupCandidates(
            editor.document.uri.fsPath,
            vscode.workspace.workspaceFolders,
        );
        if (fileCandidates.length === 0) {
            vscode.window.showInformationMessage(NO_INDEXED_ENTITY_MESSAGE);
            return null;
        }

        const lookup = await findEntityByLocation(
            apiClient,
            fileCandidates,
            editor.selection.active.line + 1,
        );
        return handleEntityLookupResult(lookup.result, true);
    }

    function handleEntityLookupResult(
        result: Awaited<ReturnType<typeof apiClient.getEntityByLocation>>,
        showInfoOnMissing: boolean,
    ): BackendCodeEntity | null {
        if (result.ok) {
            return result.data;
        }

        if (result.error === "entity_not_found") {
            if (showInfoOnMissing) {
                vscode.window.showInformationMessage(NO_INDEXED_ENTITY_MESSAGE);
            }
            return null;
        }

        vscode.window.showErrorMessage("TailEvents backend is unavailable or returned an error.");
        return null;
    }

    async function revealSidebar(): Promise<void> {
        try {
            await vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        } catch {
            // Fallback to the container reveal command below.
        }
        await vscode.commands.executeCommand(`workbench.view.extension.${VIEW_CONTAINER_ID}`);
    }

    async function collectOnboardingCandidates(
        workspaceFolders: readonly vscode.WorkspaceFolder[],
    ) {
        return collectWorkspacePythonCandidates(workspaceFolders);
    }

    async function collectWorkspacePythonCandidates(
        workspaceFolders: readonly vscode.WorkspaceFolder[],
    ) {
        const candidates = [];
        for (const folder of workspaceFolders) {
            const matches = await vscode.workspace.findFiles(
                new vscode.RelativePattern(folder, "**/*.py"),
            );
            candidates.push(
                ...buildOnboardingCandidates(
                    folder.uri.fsPath,
                    matches.map((item) => item.fsPath),
                ),
            );
        }
        return candidates.sort((left, right) => {
            const byWorkspacePath = left.workspaceFilePath.localeCompare(right.workspaceFilePath);
            if (byWorkspacePath !== 0) {
                return byWorkspacePath;
            }
            return left.absolutePath.localeCompare(right.absolutePath);
        });
    }

}

export function deactivate(): void {
    return;
}
