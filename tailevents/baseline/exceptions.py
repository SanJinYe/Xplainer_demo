"""Baseline onboarding exceptions."""


class BaselineOnboardingValidationError(ValueError):
    """Raised when a baseline onboarding request is invalid."""


__all__ = ["BaselineOnboardingValidationError"]
