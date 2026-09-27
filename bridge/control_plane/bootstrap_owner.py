"""Explicit local/admin bootstrap for the first Control Plane owner."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from core.context_registry import ContextRegistry
from core.contracts import (
    ActorV1,
    DEFAULT_ROLE_CAPABILITIES,
    EventEnvelopeV1,
    OrganizationRecordV1,
    RoleAssignmentV1,
    ScopeGrantV1,
    ScopeV1,
)
from core.postgres_persistence import PostgresPersistenceAdapter

from .event_store import EventStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an explicitly requested first Commerce Brain owner."
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--actor-role", default="OWNER")
    return parser


def run(arguments: argparse.Namespace) -> int:
    if not arguments.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required")
    adapter = PostgresPersistenceAdapter(arguments.database_url)
    try:
        registry = ContextRegistry(adapter)
        now = datetime.now(timezone.utc).isoformat()
        scope = ScopeV1.create(organization_id=arguments.organization_id)
        organization = OrganizationRecordV1.create(
            organization_id=arguments.organization_id,
            name=arguments.organization_name,
            created_at=now,
            updated_at=now,
        )
        actor = ActorV1.create(
            actor_id=arguments.actor_id,
            actor_type="HUMAN",
            role=arguments.actor_role,
            scope=scope,
        )
        assignment = RoleAssignmentV1.create(
            assignment_id=f"assignment_{arguments.actor_id}",
            actor_id=arguments.actor_id,
            role=arguments.actor_role,
            scope=scope,
        )
        grant = ScopeGrantV1.create(
            grant_id=f"grant_{arguments.actor_id}",
            actor_id=arguments.actor_id,
            scope=scope,
            capabilities=list(DEFAULT_ROLE_CAPABILITIES[arguments.actor_role.upper()]),
            effect="ALLOW",
        )
        with adapter.transaction():
            registry.register(organization, upsert=True)
            registry.register(actor, upsert=True)
            registry.register(assignment, upsert=True)
            registry.register(grant, upsert=True)
            event = EventEnvelopeV1.create(
                event_id=f"event_bootstrap_{arguments.actor_id}",
                event_type="security.bootstrap_owner",
                scope=scope,
                actor=actor,
                source="bootstrap_cli",
                occurred_at=now,
                received_at=now,
                idempotency_key=f"bootstrap:{arguments.organization_id}:{arguments.actor_id}",
                payload={
                    "operation": "bootstrap_owner",
                    "organization_id": arguments.organization_id,
                    "actor_id": arguments.actor_id,
                    "result": "ACCEPTED",
                    "can_execute": False,
                    "platform_write_attempted": False,
                },
            )
            EventStore(adapter).append(event)
        print(
            f"bootstrapped organization={organization.organization_id} "
            f"actor={actor.actor_id} role={assignment.role} schema={adapter.schema_version}"
        )
        return 0
    finally:
        adapter.close()


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
