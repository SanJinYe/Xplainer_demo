import * as vscode from "vscode";

import type { TailEventsApi } from "./api-client";
import type { CodingProfileSyncItemPayload } from "./types";


const PROFILES_KEY = "tailevents.profiles";
const DEFAULT_PROFILE_KEY = "tailevents.defaultProfileId";
const CODE_PROFILE_KEY = "tailevents.codeProfilePreferenceId";
const EXPLAIN_PROFILE_KEY = "tailevents.explainProfilePreferenceId";

type StoredProfileMetadata = Omit<CodingProfileSyncItemPayload, "api_key">;


export class CodingProfileManager {
    public constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly apiClient: TailEventsApi,
    ) {}

    public getCodeProfilePreferenceId(): string | null {
        return this.context.globalState.get<string>(CODE_PROFILE_KEY) ?? null;
    }

    public getExplainProfilePreferenceId(): string | null {
        return this.context.globalState.get<string>(EXPLAIN_PROFILE_KEY) ?? null;
    }

    public async syncToBackend(): Promise<void> {
        if (!this.apiClient.syncCodingProfiles) {
            return;
        }

        const profiles = await this.loadProfiles();
        await this.apiClient.syncCodingProfiles({
            profiles,
        });
    }

    public async showManageProfilesQuickPick(): Promise<void> {
        const profileItems = await this.loadProfiles();
        const quickPick = vscode.window.createQuickPick<
            vscode.QuickPickItem & { action: string; profileId?: string }
        >();
        quickPick.title = "Manage Profiles";
        quickPick.items = [
            {
                label: "$(add) Add Claude Profile",
                action: "add-claude",
            },
            {
                label: "$(add) Add OpenRouter Profile",
                action: "add-openrouter",
            },
            ...profileItems.map((profile) => ({
                label: `$(star-empty) Set Default: ${profile.label}`,
                description: `${profile.backend} / ${profile.model}`,
                detail:
                    profile.profile_id === this.getDefaultProfileId()
                        ? "Current default profile"
                        : undefined,
                action: "set-default",
                profileId: profile.profile_id,
            })),
            ...profileItems.map((profile) => ({
                label: `$(trash) Remove ${profile.label}`,
                description: `${profile.backend} / ${profile.model}`,
                action: "remove-profile",
                profileId: profile.profile_id,
            })),
        ];

        const selection = await new Promise<
            (vscode.QuickPickItem & { action: string; profileId?: string }) | undefined
        >((resolve) => {
            quickPick.onDidAccept(() => resolve(quickPick.selectedItems[0]));
            quickPick.onDidHide(() => resolve(undefined));
            quickPick.show();
        });
        quickPick.dispose();

        if (!selection) {
            return;
        }

        switch (selection.action) {
            case "add-claude":
                await this.addProfile("claude");
                return;
            case "add-openrouter":
                await this.addProfile("openrouter");
                return;
            case "set-default":
                if (selection.profileId) {
                    await this.context.globalState.update(DEFAULT_PROFILE_KEY, selection.profileId);
                }
                return;
            case "remove-profile":
                if (selection.profileId) {
                    await this.removeProfile(selection.profileId);
                }
                return;
            default:
                return;
        }
    }

    private async loadProfiles(): Promise<CodingProfileSyncItemPayload[]> {
        const metadata = this.context.globalState.get<StoredProfileMetadata[]>(PROFILES_KEY) ?? [];
        const defaultProfileId =
            this.context.globalState.get<string>(DEFAULT_PROFILE_KEY) ?? null;
        const profiles: CodingProfileSyncItemPayload[] = [];
        for (const item of metadata) {
            profiles.push({
                ...item,
                is_default: item.profile_id === defaultProfileId,
                api_key: await this.context.secrets.get(this.secretKey(item.profile_id)),
            });
        }
        return profiles;
    }

    private async addProfile(backend: "claude" | "openrouter"): Promise<void> {
        const label = await vscode.window.showInputBox({
            prompt: `Profile label for ${backend}`,
            ignoreFocusOut: true,
        });
        if (!label?.trim()) {
            return;
        }

        const model = await vscode.window.showInputBox({
            prompt: `Model name for ${backend}`,
            ignoreFocusOut: true,
        });
        if (!model?.trim()) {
            return;
        }

        const apiKey = await vscode.window.showInputBox({
            prompt: `API key for ${backend}`,
            password: true,
            ignoreFocusOut: true,
        });
        if (!apiKey?.trim()) {
            return;
        }

        const profileId = `${backend}:${Date.now()}`;
        const metadata = this.context.globalState.get<StoredProfileMetadata[]>(PROFILES_KEY) ?? [];
        metadata.push({
            profile_id: profileId,
            label: label.trim(),
            backend,
            model: model.trim(),
            is_default: false,
        });
        await this.context.globalState.update(PROFILES_KEY, metadata);
        await this.context.globalState.update(CODE_PROFILE_KEY, profileId);
        if (!this.getDefaultProfileId()) {
            await this.context.globalState.update(DEFAULT_PROFILE_KEY, profileId);
        }
        await this.context.secrets.store(this.secretKey(profileId), apiKey.trim());
        await this.syncToBackend();
    }

    private async removeProfile(profileId: string): Promise<void> {
        const metadata = this.context.globalState.get<StoredProfileMetadata[]>(PROFILES_KEY) ?? [];
        const nextMetadata = metadata.filter((item) => item.profile_id !== profileId);
        await this.context.globalState.update(PROFILES_KEY, nextMetadata);
        await this.context.secrets.delete(this.secretKey(profileId));
        if (this.getCodeProfilePreferenceId() === profileId) {
            await this.context.globalState.update(CODE_PROFILE_KEY, null);
        }
        if (this.getExplainProfilePreferenceId() === profileId) {
            await this.context.globalState.update(EXPLAIN_PROFILE_KEY, null);
        }
        if (this.getDefaultProfileId() === profileId) {
            await this.context.globalState.update(DEFAULT_PROFILE_KEY, null);
        }
        await this.syncToBackend();
    }

    public async showSelectCodeProfileQuickPick(): Promise<void> {
        const profileItems = await this.loadProfiles();
        const current = this.getCodeProfilePreferenceId();
        const picked = await vscode.window.showQuickPick(
            [
                {
                    label: "$(circle-large-outline) Automatic Default",
                    description: "Use default selectable profile",
                    profileId: null,
                },
                ...profileItems.map((profile) => ({
                    label: profile.label,
                    description: `${profile.backend} / ${profile.model}`,
                    detail: profile.profile_id === current ? "Currently selected for Code" : undefined,
                    profileId: profile.profile_id,
                })),
            ],
            {
                title: "Select Code Profile",
                ignoreFocusOut: true,
            },
        );
        if (!picked) {
            return;
        }
        await this.context.globalState.update(CODE_PROFILE_KEY, picked.profileId);
    }

    public async showSelectExplainProfileQuickPick(): Promise<void> {
        const profileItems = await this.loadProfiles();
        const current = this.getExplainProfilePreferenceId();
        const picked = await vscode.window.showQuickPick(
            [
                {
                    label: "$(link) Follow Code Profile",
                    description: "Reuse the effective Code profile",
                    profileId: null,
                },
                ...profileItems.map((profile) => ({
                    label: profile.label,
                    description: `${profile.backend} / ${profile.model}`,
                    detail:
                        profile.profile_id === current
                            ? "Currently selected for Explain"
                            : undefined,
                    profileId: profile.profile_id,
                })),
            ],
            {
                title: "Select Explain Profile",
                ignoreFocusOut: true,
            },
        );
        if (!picked) {
            return;
        }
        await this.context.globalState.update(EXPLAIN_PROFILE_KEY, picked.profileId);
    }

    private getDefaultProfileId(): string | null {
        return this.context.globalState.get<string>(DEFAULT_PROFILE_KEY) ?? null;
    }

    private secretKey(profileId: string): string {
        return `tailevents.profile.${profileId}.api_key`;
    }
}
