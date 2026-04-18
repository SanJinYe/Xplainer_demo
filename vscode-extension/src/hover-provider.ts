import type * as vscode from "vscode";

import type { TailEventsApi } from "./api-client";
import { findEntityByLocation } from "./location-lookup";
import { getFileLookupCandidates } from "./path-utils";
import {
    ProfileStateStore,
    resolveCodeEffectiveProfile,
    resolveExplainEffectiveProfile,
} from "./profile-resolver";
import type {
    ApiResult,
    BackendCodeEntity,
    BackendEntityExplanation,
    ExplainCommandArgs,
} from "./types";

const DEFAULT_COMMAND_ID = "tailEvents.explainCurrentSymbol";

interface HoverRuntime {
    MarkdownString: new (...args: any[]) => any;
    Hover: new (...args: any[]) => any;
}

interface HoverProviderOptions {
    apiClient: TailEventsApi;
    profileStateStore?: ProfileStateStore;
    getWorkspaceFolders: () => readonly vscode.WorkspaceFolder[] | undefined;
    getCodeProfilePreferenceId?: () => string | null;
    getExplainProfilePreferenceId?: () => string | null;
    isHoverEnabled: () => boolean;
    vscodeApi: HoverRuntime;
    commandId?: string;
}

export class TailEventsHoverProvider implements vscode.HoverProvider {
    private readonly commandId: string;

    private readonly vscodeApi: HoverRuntime;

    private readonly apiClient: TailEventsApi;

    private readonly profileStateStore: ProfileStateStore;

    private readonly getWorkspaceFolders: () => readonly vscode.WorkspaceFolder[] | undefined;

    private readonly getCodeProfilePreferenceId: () => string | null;

    private readonly getExplainProfilePreferenceId: () => string | null;

    private readonly isHoverEnabled: () => boolean;

    public constructor(options: HoverProviderOptions) {
        this.apiClient = options.apiClient;
        this.profileStateStore = options.profileStateStore ?? new ProfileStateStore(options.apiClient);
        this.getWorkspaceFolders = options.getWorkspaceFolders;
        this.getCodeProfilePreferenceId = options.getCodeProfilePreferenceId ?? (() => null);
        this.getExplainProfilePreferenceId = options.getExplainProfilePreferenceId ?? (() => null);
        this.isHoverEnabled = options.isHoverEnabled;
        this.vscodeApi = options.vscodeApi;
        this.commandId = options.commandId ?? DEFAULT_COMMAND_ID;
    }

    public async provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
        token: vscode.CancellationToken,
    ): Promise<vscode.Hover | null> {
        if (!this.isHoverEnabled()) {
            return null;
        }
        if (document.isUntitled || document.uri.scheme !== "file") {
            return null;
        }

        const wordRange = document.getWordRangeAtPosition(position);
        if (!wordRange) {
            return null;
        }

        const fileCandidates = getFileLookupCandidates(
            document.uri.fsPath,
            this.getWorkspaceFolders(),
        );
        if (fileCandidates.length === 0) {
            return null;
        }

        const abortSignal = cancellationTokenToAbortSignal(token);
        const lineNumber = position.line + 1;
        const lookup = await findEntityByLocation(
            this.apiClient,
            fileCandidates,
            lineNumber,
            abortSignal,
        );
        if (!lookup.result.ok) {
            return null;
        }

        await this.profileStateStore.ensureLoaded(abortSignal);
        const codeProfile = resolveCodeEffectiveProfile(
            this.profileStateStore.getProfiles(),
            this.getCodeProfilePreferenceId(),
        );
        const explainProfile = resolveExplainEffectiveProfile(
            this.profileStateStore.getProfiles(),
            codeProfile,
            this.getExplainProfilePreferenceId(),
        );
        if (!explainProfile.available) {
            return null;
        }

        const summaryResult = await this.apiClient.getExplanationSummary(
            lookup.result.data.entity_id,
            explainProfile.resolvedProfileId ?? undefined,
            abortSignal,
        );
        if (token.isCancellationRequested) {
            return null;
        }

        const markdown = this.buildHoverMarkdown(
            lookup.result.data,
            summaryResult.ok ? summaryResult : null,
            lookup.file,
            lineNumber,
        );
        return new this.vscodeApi.Hover(markdown, wordRange) as unknown as vscode.Hover;
    }

    private buildHoverMarkdown(
        entity: BackendCodeEntity,
        summaryResult: ApiResult<BackendEntityExplanation> | null,
        file: string,
        line: number,
    ): any {
        const markdown = new this.vscodeApi.MarkdownString(undefined, true);
        markdown.isTrusted = {
            enabledCommands: [this.commandId],
        };
        markdown.supportHtml = false;

        markdown.appendMarkdown("**");
        markdown.appendText(entity.name);
        markdown.appendMarkdown("** ");
        markdown.appendMarkdown("`");
        markdown.appendText(entity.entity_type);
        markdown.appendMarkdown("`\n\n");

        if (
            summaryResult &&
            summaryResult.ok &&
            summaryResult.data.history_source === "baseline_only"
        ) {
            markdown.appendMarkdown("`基线`\n\n");
        }

        if (entity.signature) {
            markdown.appendCodeblock(entity.signature, "python");
            markdown.appendMarkdown("\n\n");
        }

        if (summaryResult && summaryResult.ok && summaryResult.data.summary.trim().length > 0) {
            markdown.appendText(summaryResult.data.summary.trim());
            markdown.appendMarkdown("\n\n");
        }

        markdown.appendMarkdown("_");
        markdown.appendText(`${entity.event_refs.length} events recorded`);
        markdown.appendMarkdown("_");
        markdown.appendMarkdown(" · ");
        markdown.appendMarkdown(`[View Details](${buildCommandUri(this.commandId, {
            entityId: entity.entity_id,
            file,
            line,
        })})`);

        return markdown;
    }
}

function buildCommandUri(commandId: string, args: ExplainCommandArgs): string {
    return `command:${commandId}?${encodeURIComponent(JSON.stringify([args]))}`;
}

function cancellationTokenToAbortSignal(token: vscode.CancellationToken): AbortSignal {
    if (token.isCancellationRequested) {
        return AbortSignal.abort();
    }

    const controller = new AbortController();
    const disposable = token.onCancellationRequested(() => {
        controller.abort();
        disposable.dispose();
    });
    return controller.signal;
}
