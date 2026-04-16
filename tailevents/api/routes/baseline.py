"""Baseline onboarding API routes."""

from fastapi import APIRouter, Depends, HTTPException

from tailevents.api.dependencies import get_baseline_onboarding_service
from tailevents.baseline import (
    BaselineOnboardingService,
    BaselineOnboardingValidationError,
)
from tailevents.models.baseline import (
    BaselineOnboardFileRequest,
    BaselineOnboardFileResponse,
)


router = APIRouter(prefix="/baseline", tags=["baseline"])


@router.post("/onboard-file", response_model=BaselineOnboardFileResponse)
async def onboard_file(
    request: BaselineOnboardFileRequest,
    service: BaselineOnboardingService = Depends(get_baseline_onboarding_service),
) -> BaselineOnboardFileResponse:
    try:
        return await service.onboard_file(request)
    except BaselineOnboardingValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


__all__ = ["router"]
