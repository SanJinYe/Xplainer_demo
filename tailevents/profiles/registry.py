"""In-memory coding profile registry used by the API backend."""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from tailevents.config import Settings
from tailevents.explanation.llm_client import LLMClientFactory
from tailevents.models.profile import (
    CodingCapabilitiesResponse,
    CodingCapabilityState,
    CodingProfileStatusItem,
    CodingProfilesStatusResponse,
    CodingProfilesSyncRequest,
    CodingProfileSyncItem,
)
from tailevents.models.protocols import CodingProfileRegistryProtocol, LLMClientProtocol


@dataclass
class _ResolvedProfile:
    profile_id: str
    backend: str
    model: str
    api_key: Optional[str]
    source: str


class InMemoryCodingProfileRegistry(CodingProfileRegistryProtocol):
    """Keep synced coding profiles in memory and expose env fallback profiles."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._profiles: "OrderedDict[str, CodingProfileSyncItem]" = OrderedDict()

    def sync_profiles(self, request: CodingProfilesSyncRequest) -> None:
        self._profiles = OrderedDict(
            (profile.profile_id, profile.model_copy(deep=True))
            for profile in request.profiles
        )

    def get_profiles_status(self) -> CodingProfilesStatusResponse:
        profiles: list[CodingProfileStatusItem] = []

        for item in self._profiles.values():
            has_key = self._profile_has_key(item.backend, item.api_key)
            selectable, reason = self._selectable_state(item.backend, item.api_key)
            profiles.append(
                CodingProfileStatusItem(
                    profile_id=item.profile_id,
                    label=item.label,
                    backend=item.backend,
                    model=item.model,
                    source="sync",
                    has_key=has_key,
                    is_default=item.is_default,
                    selectable=selectable,
                    reason=reason,
                )
            )

        fallback = self._build_env_fallback_status()
        if fallback is not None:
            profiles.append(fallback)

        return CodingProfilesStatusResponse(profiles=profiles)

    def get_capabilities(self) -> CodingCapabilitiesResponse:
        return CodingCapabilitiesResponse(
            repo_observe=CodingCapabilityState(available=True),
            multi_file=CodingCapabilityState(available=True),
            mcp=CodingCapabilityState(
                available=False,
                reason="not implemented in Phase 4",
            ),
            skills=CodingCapabilityState(
                available=False,
                reason="not implemented in Phase 4",
            ),
        )

    def get_llm_client(self, profile_id: Optional[str] = None) -> LLMClientProtocol:
        resolved = self._resolve_profile(profile_id)
        settings_like = self._build_settings_like(resolved)
        return LLMClientFactory.create(settings_like)

    def _resolve_profile(self, profile_id: Optional[str]) -> _ResolvedProfile:
        if profile_id:
            if profile_id in self._profiles:
                profile = self._profiles[profile_id]
                selectable, reason = self._selectable_state(profile.backend, profile.api_key)
                if not selectable:
                    raise ValueError(reason or f"Profile is not selectable: {profile_id}")
                return _ResolvedProfile(
                    profile_id=profile.profile_id,
                    backend=profile.backend,
                    model=profile.model,
                    api_key=profile.api_key,
                    source="sync",
                )

            env_profile = self._build_env_fallback()
            if env_profile is not None and env_profile.profile_id == profile_id:
                selectable, reason = self._selectable_state(
                    env_profile.backend,
                    env_profile.api_key,
                )
                if not selectable:
                    raise ValueError(reason or f"Profile is not selectable: {profile_id}")
                return env_profile

            raise ValueError(f"Unknown coding profile: {profile_id}")

        for profile in self._profiles.values():
            if not profile.is_default:
                continue
            selectable, _ = self._selectable_state(profile.backend, profile.api_key)
            if selectable:
                return _ResolvedProfile(
                    profile_id=profile.profile_id,
                    backend=profile.backend,
                    model=profile.model,
                    api_key=profile.api_key,
                    source="sync",
                )

        for profile in self._profiles.values():
            selectable, _ = self._selectable_state(profile.backend, profile.api_key)
            if selectable:
                return _ResolvedProfile(
                    profile_id=profile.profile_id,
                    backend=profile.backend,
                    model=profile.model,
                    api_key=profile.api_key,
                    source="sync",
                )

        env_profile = self._build_env_fallback()
        if env_profile is not None:
            selectable, reason = self._selectable_state(
                env_profile.backend,
                env_profile.api_key,
            )
            if selectable:
                return env_profile
            raise ValueError(reason or "Environment fallback profile is not selectable")

        raise ValueError("No coding profile is available")

    def _build_env_fallback_status(self) -> Optional[CodingProfileStatusItem]:
        env_profile = self._build_env_fallback()
        if env_profile is None:
            return None
        selectable, reason = self._selectable_state(
            env_profile.backend,
            env_profile.api_key,
        )
        return CodingProfileStatusItem(
            profile_id=env_profile.profile_id,
            label="Environment Default",
            backend=env_profile.backend,
            model=env_profile.model,
            source="env_fallback",
            has_key=self._profile_has_key(env_profile.backend, env_profile.api_key),
            is_default=not any(profile.is_default for profile in self._profiles.values()),
            selectable=selectable,
            reason=reason,
        )

    def _build_env_fallback(self) -> Optional[_ResolvedProfile]:
        backend = self._settings.llm_backend.lower().strip()
        if not backend:
            return None

        if backend == "ollama":
            return _ResolvedProfile(
                profile_id="env:ollama",
                backend="ollama",
                model=self._settings.ollama_model,
                api_key=None,
                source="env_fallback",
            )

        if backend == "claude":
            return _ResolvedProfile(
                profile_id="env:claude",
                backend="claude",
                model=self._settings.claude_model,
                api_key=self._settings.claude_api_key,
                source="env_fallback",
            )

        if backend == "openrouter":
            return _ResolvedProfile(
                profile_id="env:openrouter",
                backend="openrouter",
                model=self._settings.openrouter_model,
                api_key=self._settings.openrouter_api_key,
                source="env_fallback",
            )

        return None

    def _build_settings_like(self, resolved: _ResolvedProfile) -> dict[str, object]:
        if resolved.backend == "ollama":
            return {
                "llm_backend": "ollama",
                "ollama_base_url": self._settings.ollama_base_url,
                "ollama_model": resolved.model,
            }

        if resolved.backend == "claude":
            return {
                "llm_backend": "claude",
                "claude_api_key": resolved.api_key,
                "claude_model": resolved.model,
                "proxy_url": self._settings.proxy_url,
            }

        if resolved.backend == "openrouter":
            return {
                "llm_backend": "openrouter",
                "openrouter_api_key": resolved.api_key,
                "openrouter_model": resolved.model,
                "openrouter_base_url": self._settings.openrouter_base_url,
                "openrouter_site_url": self._settings.openrouter_site_url,
                "openrouter_app_name": self._settings.openrouter_app_name,
                "proxy_url": self._settings.proxy_url,
            }

        raise ValueError(f"Unsupported coding profile backend: {resolved.backend}")

    def _selectable_state(
        self,
        backend: str,
        api_key: Optional[str],
    ) -> tuple[bool, Optional[str]]:
        normalized = backend.lower().strip()
        if normalized == "ollama":
            return (True, None)
        if normalized in {"claude", "openrouter"}:
            if api_key:
                return (True, None)
            return (False, "missing api key")
        return (False, f"unsupported backend: {backend}")

    def _profile_has_key(self, backend: str, api_key: Optional[str]) -> bool:
        normalized = backend.lower().strip()
        if normalized == "ollama":
            return True
        return bool(api_key)


__all__ = ["InMemoryCodingProfileRegistry"]
