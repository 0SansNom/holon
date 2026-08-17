"""Knowledge public API package."""

from .ontologies import router as ontologies_router
from .path_rewrite import ApiPathRewriteMiddleware

__all__ = ["ApiPathRewriteMiddleware", "ontologies_router"]
