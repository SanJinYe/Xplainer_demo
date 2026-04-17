import * as vscode from "vscode";

import type { TailEventsApi } from "./api-client";
import type { CodingProfileSyncItemPayload } from "./types";


const PROFILES_KEY = "tailevents.profiles";
const DEFAULT_PROFILE_KEY = "tailevents.defaultProfileId";
const CODING_PROFILE_KEY = "tailevents.codingProfileId";

type StoredProfileMetadata = Omit<CodingProfileSyncItemPayload, "api_key">;


export class CodingProfileManager {
    public constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly apiClient: TailEventsApi,
    ) {}

    public getSelectedProfileId(): string | null {
        return this.context.globalState.get<string>(CODING_PROFILE_KEY) ?? null;
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
        quickPick.title = "Manage Coding Profiles";
        quickPick.items = [
            {
                label: "$(add) Add Claude Profile",
                action: "add-claude",
            },
            {
                label: "$(add) Add OpenRouter Profile",
                action: "add-openrouter",
            },
            {
                label: "$(circle-large-outline) Use Environment Default",
                action: "use-env",
            },
            ...profileItems.map((profile) => ({
                label: profile.label,
                description: `${profile.backend} / ${profile.model}`,
                detail:
                    profile.profile_id === this.getSelectedProfileId()
                        ? "Currently selected"
                        : undefined,
                action: "select-profile",
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
            case "use-env":
                await this.context.globalState.update(CODING_PROFILE_KEY, null);
                return;
            case "select-profile":
                if (selection.profileId) {
                    await this.context.globalState.update(CODING_PROFILE_KEY, selection.profileId);
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
        await this.context.globalState.update(CODING_PROFILE_KEY, profileId);
        await this.context.secrets.store(this.secretKey(profileId), apiKey.trim());
        await this.syncToBackend();
    }

    private async removeProfile(profileId: string): Promise<void> {
        const metadata = this.context.globalState.get<StoredProfileMetadata[]>(PROFILES_KEY) ?? [];
        const nextMetadata = metadata.filter((item) => item.profile_id !== profileId);
        await this.context.globalState.update(PROFILES_KEY, nextMetadata);
        await this.context.secrets.delete(this.secretKey(profileId));
        if (this.getSelectedProfileId() === profileId) {
            await this.context.globalState.update(CODING_PROFILE_KEY, null);
        }
        await this.syncToBackend();
    }

    private secretKey(profileId: string): string {
        return `tailevents.profile.${profileId}.api_key`;
    }
}
