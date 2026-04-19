"""Minimal capability policy for the coding runtime."""

from typing import Optional

from tailevents.models.profile import CodingCapabilitiesResponse
from tailevents.models.protocols import CodingProfileRegistryProtocol
from tailevents.models.task import CodingTaskRequestedCapability


class CapabilityPolicy:
    """Resolve capability availability without introducing a new public contract."""

    _CORE_ENABLED = {
        "code": True,
        "explain": True,
        "graph": True,
        "graphrag": False,
    }

    def __init__(
        self,
        profile_registry: Optional[CodingProfileRegistryProtocol] = None,
    ) -> None:
        self._profile_registry = profile_registry

    def is_runtime_capability_enabled(self, name: str) -> bool:
        return self._CORE_ENABLED.get(name, False)

    def resolve_requested_lanes(
        self,
        requested_capabilities: list[CodingTaskRequestedCapability],
    ) -> set[str]:
        available = self._profile_capabilities()
        allowed: set[str] = set()
        for capability in requested_capabilities:
            state = getattr(available, capability)
            if state.available:
                allowed.add(capability)
        return allowed

    def _profile_capabilities(self) -> CodingCapabilitiesResponse:
        if self._profile_registry is None:
            return CodingCapabilitiesResponse.model_validate(
                {
                    "repo_observe": {"available": True},
                    "multi_file": {"available": True},
                    "mcp": {"available": False, "reason": "not configured"},
                    "skills": {"available": False, "reason": "not configured"},
                }
            )
        return self._profile_registry.get_capabilities()


__all__ = ["CapabilityPolicy"]
