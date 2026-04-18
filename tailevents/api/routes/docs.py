"""Authorized docs sync API routes."""

from fastapi import APIRouter, Depends

from tailevents.api.dependencies import AppContainer, get_container
from tailevents.models.docs import DocsSyncRequest, DocsSyncResponse


router = APIRouter(prefix="/docs", tags=["docs"])


@router.post("/sync", response_model=DocsSyncResponse)
async def sync_docs(
    request: DocsSyncRequest,
    container: AppContainer = Depends(get_container),
) -> DocsSyncResponse:
    return await container.doc_retriever.sync_documents(request.documents)


__all__ = ["router"]
