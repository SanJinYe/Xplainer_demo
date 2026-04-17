"""Coding capability discovery routes."""

from fastapi import APIRouter, Depends

from tailevents.api.dependencies import get_profile_registry
from tailevents.models.profile import CodingCapabilitiesResponse
from tailevents.models.protocols import CodingProfileRegistryProtocol


router = APIRouter(prefix="/coding", tags=["coding"])


@router.get("/capabilities", response_model=CodingCapabilitiesResponse)
async def get_coding_capabilities(
    registry: CodingProfileRegistryProtocol = Depends(get_profile_registry),
) -> CodingCapabilitiesResponse:
    return registry.get_capabilities()


__all__ = ["router"]
