import path from "node:path";

import * as vscode from "vscode";

import { TailEventsApiClient } from "./api-client";
import { TailEventsHoverProvider } from "./hover-provider";
import { findEntityByLocation } from "./location-lookup";
import { getFileLookupCandidates } from "./path-utils";
import { TailEventsSidebarProvider } from "./sidebar-provider";
import type { BackendCodeEntity, ExplainCommandArgs } from "./types";

const COMMAND_EXPLAIN_CURRENT_SYMBOL = "tailEvents.explainCurrentSymbol";
const COMMAND_OPEN_PANEL = "tailEvents.openPanel";
const COMMAND_REFRESH_PANEL = "tailEvents.refreshPanel";
const VIEW_CONTAINER_ID = "tailevents-sidebar";
const VIEW_ID = "tailevents.sidebarView";
const DEFAULT_BASE_URL = "http://127.0.0.1:8766/api/v1";
const DEFAULT_TIMEOUT_MS = 5000;
const NO_INDEXED_ENTITY_MESSAGE = "No indexed entity at cursor position.";

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
        templatePath: path.join(context.extensionPath, "media", "sidebar.html"),
        runtime: {
            getActiveEditor: () => vscode.window.activeTextEditor ?? null,
            getWorkspaceFolders: () => vscode.workspace.workspaceFolders,
            replaceDocumentContent: async (editor, content) => {
                const lastLineIndex = Math.max(editor.document.lineCount - 1, 0);
                const lastLine = editor.document.lineAt(lastLineIndex);
                const fullRange = new vscode.Range(
                    0,
                    0,
                    lastLineIndex,
                    lastLine.text.length,
                );
                return editor.edit((builder) => {
                    builder.replace(fullRange, content);
                });
            },
            saveDocument: async (document) => document.save(),
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
                    await sidebarProvider.loadEntity(args.entityId);
                    return;
                }

                const entity = await resolveEntityAtActiveLocation(args);
                if (!entity) {
                    return;
                }

                await revealSidebar();
                await sidebarProvider.loadEntity(entity.entity_id);
            },
        ),
        vscode.commands.registerCommand(COMMAND_OPEN_PANEL, async () => {
            await revealSidebar();
        }),
        vscode.commands.registerCommand(COMMAND_REFRESH_PANEL, async () => {
            const currentEntityId = sidebarProvider.getCurrentEntityId();
            if (currentEntityId) {
                await sidebarProvider.loadEntity(currentEntityId);
                return;
            }

            const entity = await resolveEntityAtActiveLocation();
            if (!entity) {
                return;
            }

            await revealSidebar();
            await sidebarProvider.loadEntity(entity.entity_id);
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
}

export function deactivate(): void {
    return;
}
