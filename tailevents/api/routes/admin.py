"""Admin API routes."""

from fastapi import APIRouter, Depends

from tailevents.api.dependencies import AppContainer, get_container


router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reindex")
async def reindex(
    container: AppContainer = Depends(get_container),
) -> dict[str, int]:
    return await container.reindex_all()


@router.get("/stats")
async def stats(
    container: AppContainer = Depends(get_container),
) -> dict[str, float | int]:
    return await container.get_admin_stats()


@router.post("/cache/clear")
async def clear_cache(
    container: AppContainer = Depends(get_container),
) -> dict[str, float | int]:
    return await container.clear_cache()


@router.post("/reset-state")
async def reset_state(
    container: AppContainer = Depends(get_container),
) -> dict[str, int]:
    return await container.reset_state()


@router.get("/health")
async def health(
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    return await container.health()


__all__ = ["router"]
