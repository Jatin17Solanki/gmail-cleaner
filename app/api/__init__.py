"""API routes module."""

from .status import router as status_router
from .actions import router as actions_router
from .auth_gate import router as auth_gate_router

__all__ = ["status_router", "actions_router", "auth_gate_router"]
