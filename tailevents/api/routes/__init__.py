"""Public route exports."""

from tailevents.api.routes.admin import router as admin_router
from tailevents.api.routes.baseline import router as baseline_router
from tailevents.api.routes.coding import router as coding_router
from tailevents.api.routes.entities import router as entities_router
from tailevents.api.routes.events import router as events_router
from tailevents.api.routes.explanations import router as explanations_router
from tailevents.api.routes.relations import router as relations_router

__all__ = [
    "admin_router",
    "baseline_router",
    "coding_router",
    "entities_router",
    "events_router",
    "explanations_router",
    "relations_router",
]
