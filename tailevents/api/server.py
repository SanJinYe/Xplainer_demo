"""FastAPI application assembly."""

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tailevents.api.dependencies import build_lifespan
from tailevents.api.routes import (
    admin_router,
    baseline_router,
    coding_capabilities_router,
    coding_router,
    docs_router,
    entities_router,
    events_router,
    explanations_router,
    host_router,
    profiles_router,
    relations_router,
)
from tailevents.config import Settings
from tailevents.explanation import (
    EntityExplanationNotFoundError,
    InvalidDetailLevelError,
    LLMClientError,
)
from tailevents.ingestion import IngestionValidationError
from tailevents.models.protocols import DocRetrieverProtocol, LLMClientProtocol
from tailevents.storage.exceptions import RecordNotFoundError


def create_app(
    settings: Optional[Settings] = None,
    llm_client: Optional[LLMClientProtocol] = None,
    doc_retriever: Optional[DocRetrieverProtocol] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="TailEvents API",
        version="0.1.0",
        lifespan=build_lifespan(
            settings=settings,
            llm_client=llm_client,
            doc_retriever=doc_retriever,
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(events_router, prefix="/api/v1")
    app.include_router(host_router, prefix="/api/v1")
    app.include_router(baseline_router, prefix="/api/v1")
    app.include_router(coding_capabilities_router, prefix="/api/v1")
    app.include_router(coding_router, prefix="/api/v1")
    app.include_router(docs_router, prefix="/api/v1")
    app.include_router(entities_router, prefix="/api/v1")
    app.include_router(explanations_router, prefix="/api/v1")
    app.include_router(profiles_router, prefix="/api/v1")
    app.include_router(relations_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")

    @app.exception_handler(EntityExplanationNotFoundError)
    async def handle_missing_entity(
        request: Request,
        exc: EntityExplanationNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RecordNotFoundError)
    async def handle_missing_record(
        request: Request,
        exc: RecordNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidDetailLevelError)
    async def handle_invalid_detail(
        request: Request,
        exc: InvalidDetailLevelError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(LLMClientError)
    async def handle_llm_error(
        request: Request,
        exc: LLMClientError,
    ) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(IngestionValidationError)
    async def handle_ingestion_validation(
        request: Request,
        exc: IngestionValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": [issue.model_dump(mode="json") for issue in exc.issues]
            },
        )

    return app


__all__ = ["create_app"]
