"""Internal capability registry for the coding runtime."""

from dataclasses import dataclass

from tailevents.coding.capability.base import RuntimeCapabilityProtocol


@dataclass(frozen=True)
class CapabilityRegistration:
    """One registered internal capability."""

    name: str
    capability: RuntimeCapabilityProtocol
    enabled: bool


class CapabilityRegistry:
    """Store runtime capabilities behind stable names."""

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        name: str,
        capability: RuntimeCapabilityProtocol,
        *,
        enabled: bool = True,
    ) -> None:
        self._registrations[name] = CapabilityRegistration(
            name=name,
            capability=capability,
            enabled=enabled,
        )

    def get(self, name: str) -> RuntimeCapabilityProtocol:
        registration = self._registrations.get(name)
        if registration is None:
            raise KeyError(f"Capability is not registered: {name}")
        return registration.capability

    def require_enabled(self, name: str) -> RuntimeCapabilityProtocol:
        registration = self._registrations.get(name)
        if registration is None:
            raise KeyError(f"Capability is not registered: {name}")
        if not registration.enabled:
            raise ValueError(f"Capability is disabled: {name}")
        return registration.capability

    def is_enabled(self, name: str) -> bool:
        registration = self._registrations.get(name)
        return registration.enabled if registration is not None else False

    def names(self) -> list[str]:
        return sorted(self._registrations)


__all__ = ["CapabilityRegistration", "CapabilityRegistry"]
