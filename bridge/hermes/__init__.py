"""Local Hermes Slow Brain contracts and fixture adapter."""

from .adapter import (
    FixtureHermesBackend,
    HermesCoordinator,
    UnavailableHermesBackend,
    role_catalog,
)
from .contracts import (
    HermesContractError,
    SlowResultV1,
    SlowTaskV1,
    build_task,
)
from .tasks import (
    FORBIDDEN_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    HermesToolRegistry,
    tool_catalog,
)
from .slow_task import (
    DRAFT_KINDS,
    HERMES_ROLES,
    SlowTaskContractError,
    HermesSlowLoop,
    build_role_catalog,
)

__all__ = [
    "DRAFT_KINDS",
    "FORBIDDEN_TOOLS",
    "HERMES_ROLES",
    "HermesContractError",
    "HermesCoordinator",
    "HermesSlowLoop",
    "HermesToolRegistry",
    "READ_TOOLS",
    "SlowResultV1",
    "SlowTaskContractError",
    "SlowTaskV1",
    "UnavailableHermesBackend",
    "WRITE_TOOLS",
    "build_task",
    "build_role_catalog",
    "role_catalog",
    "tool_catalog",
]
