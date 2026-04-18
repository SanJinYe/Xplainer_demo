import type { TailEventsApi } from "./api-client";
import type {
    BackendCodingCapabilitiesResponse,
    BackendCodingProfileStatusItem,
    CapabilitySummaryViewModel,
    EffectiveProfileViewModel,
} from "./types";

const NO_AVAILABLE_PROFILE_LABEL = "No available profile";
const NO_AVAILABLE_PROFILE_REASON = "No selectable coding profile is available.";
const BACKEND_DEFAULT_LABEL = "Backend Default";

export class ProfileStateStore {
    private profiles: BackendCodingProfileStatusItem[] = [];

    private capabilities: BackendCodingCapabilitiesResponse | null = null;

    public constructor(private readonly apiClient: TailEventsApi) {}

    public async ensureLoaded(signal?: AbortSignal): Promise<void> {
        if (this.profiles.length > 0 || this.capabilities) {
            return;
        }
        await this.refresh(signal);
    }

    public async refresh(signal?: AbortSignal): Promise<void> {
        const [profilesResult, capabilitiesResult] = await Promise.all([
            this.apiClient.getCodingProfilesStatus?.(signal) ?? Promise.resolve(null),
            this.apiClient.getCodingCapabilities?.(signal) ?? Promise.resolve(null),
        ]);

        if (profilesResult?.ok) {
            this.profiles = profilesResult.data.profiles;
        }
        if (capabilitiesResult?.ok) {
            this.capabilities = capabilitiesResult.data;
        }
    }

    public getProfiles(): BackendCodingProfileStatusItem[] {
        return [...this.profiles];
    }

    public getCapabilities(): BackendCodingCapabilitiesResponse | null {
        return this.capabilities;
    }
}

export function resolveCodeEffectiveProfile(
    profiles: BackendCodingProfileStatusItem[],
    preferenceId: string | null,
): EffectiveProfileViewModel {
    if (preferenceId) {
        const explicit = profiles.find((profile) => profile.profile_id === preferenceId);
        if (!explicit) {
            return unavailableProfile(
                preferenceId,
                preferenceId,
                `Selected code profile was not found: ${preferenceId}`,
            );
        }
        return mapStatusItem(explicit, {
            preferenceId,
            followsCode: false,
        });
    }

    const resolved =
        profiles.find((profile) => profile.is_default && profile.selectable) ??
        profiles.find((profile) => profile.selectable) ??
        null;
    if (!resolved) {
        if (profiles.length === 0) {
            return backendDefaultProfile();
        }
        return unavailableProfile(
            null,
            null,
            NO_AVAILABLE_PROFILE_REASON,
        );
    }
    return mapStatusItem(resolved, {
        preferenceId: null,
        followsCode: false,
    });
}

export function resolveExplainEffectiveProfile(
    profiles: BackendCodingProfileStatusItem[],
    codeProfile: EffectiveProfileViewModel,
    preferenceId: string | null,
): EffectiveProfileViewModel {
    if (!preferenceId) {
        return {
            ...codeProfile,
            preferenceId: null,
            followsCode: true,
        };
    }

    const explicit = profiles.find((profile) => profile.profile_id === preferenceId);
    if (!explicit) {
        return unavailableProfile(
            preferenceId,
            preferenceId,
            `Selected explain profile was not found: ${preferenceId}`,
        );
    }
    return mapStatusItem(explicit, {
        preferenceId,
        followsCode: false,
    });
}

export function buildCapabilitySummary(
    capabilities: BackendCodingCapabilitiesResponse | null,
): CapabilitySummaryViewModel {
    if (!capabilities) {
        return {
            available: [],
            unavailableCount: 0,
        };
    }

    const items = [
        ["repo_observe", capabilities.repo_observe],
        ["multi_file", capabilities.multi_file],
        ["mcp", capabilities.mcp],
        ["skills", capabilities.skills],
    ] as const;

    return {
        available: items
            .filter(([, state]) => state.available)
            .map(([key, state]) => ({
                key,
                available: state.available,
                reason: state.reason ?? null,
            })),
        unavailableCount: items.filter(([, state]) => !state.available).length,
    };
}

function mapStatusItem(
    item: BackendCodingProfileStatusItem,
    options: {
        preferenceId: string | null;
        followsCode: boolean;
    },
): EffectiveProfileViewModel {
    return {
        preferenceId: options.preferenceId,
        resolvedProfileId: item.profile_id,
        label: item.label,
        backend: item.backend,
        model: item.model,
        source: item.source,
        followsCode: options.followsCode,
        available: item.selectable,
        selectable: item.selectable,
        reason: item.selectable ? null : item.reason ?? "Profile is not selectable.",
    };
}

function unavailableProfile(
    preferenceId: string | null,
    resolvedProfileId: string | null,
    reason: string,
): EffectiveProfileViewModel {
    return {
        preferenceId,
        resolvedProfileId,
        label: NO_AVAILABLE_PROFILE_LABEL,
        backend: null,
        model: null,
        source: null,
        followsCode: false,
        available: false,
        selectable: false,
        reason,
    };
}

function backendDefaultProfile(): EffectiveProfileViewModel {
    return {
        preferenceId: null,
        resolvedProfileId: null,
        label: BACKEND_DEFAULT_LABEL,
        backend: null,
        model: null,
        source: null,
        followsCode: false,
        available: true,
        selectable: true,
        reason: null,
    };
}
