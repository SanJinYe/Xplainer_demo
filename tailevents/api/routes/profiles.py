"""Coding profile API routes."""

from fastapi import APIRouter, Depends

from tailevents.api.dependencies import get_profile_registry
from tailevents.models.profile import (
    CodingProfilesStatusResponse,
    CodingProfilesSyncRequest,
)
from tailevents.models.protocols import CodingProfileRegistryProtocol


router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("/sync", status_code=204)
async def sync_coding_profiles(
    request: CodingProfilesSyncRequest,
    registry: CodingProfileRegistryProtocol = Depends(get_profile_registry),
) -> None:
    registry.sync_profiles(request)


@router.get("/status", response_model=CodingProfilesStatusResponse)
async def get_coding_profiles_status(
    registry: CodingProfileRegistryProtocol = Depends(get_profile_registry),
) -> CodingProfilesStatusResponse:
    return registry.get_profiles_status()


__all__ = ["router"]
