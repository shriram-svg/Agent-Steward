"""
Tool implementations for Steward's Claude session.
Each function corresponds to a tool Claude can call.
"""
import json
import logging
import asyncio
import asyncpg
import httpx
from .config import settings

logger = logging.getLogger(__name__)


async def get_tasks(status: str | None = None, limit: int = 50) -> dict:
    """Get tasks from Task Manager."""
    params = {"limit": limit}
    if status:
        params["status"] = status
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.task_manager_url}/tasks", params=params)
            r.raise_for_status()
            tasks = r.json()
            return {"tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"error": str(e)}


async def get_plan(plan_id: str) -> dict:
    """Get a specific plan and its tasks."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            plan_r = await client.get(f"{settings.task_manager_url}/plans/{plan_id}")
            tasks_r = await client.get(f"{settings.task_manager_url}/plans/{plan_id}/tasks")
            plan = plan_r.json() if plan_r.is_success else {}
            tasks = tasks_r.json() if tasks_r.is_success else []
            return {"plan": plan, "tasks": tasks}
    except Exception as e:
        return {"error": str(e)}


async def cancel_tasks(task_ids: list[str] | None = None, all_non_terminal: bool = False) -> dict:
    """Cancel tasks directly via Postgres."""
    try:
        conn = await asyncpg.connect(f"{settings.postgres_dsn}/task_manager")
        try:
            if all_non_terminal:
                result = await conn.execute(
                    "UPDATE tasks SET status = 'cancelled', updated_at = NOW() "
                    "WHERE status NOT IN ('completed', 'failed', 'cancelled')"
                )
            elif task_ids:
                result = await conn.execute(
                    "UPDATE tasks SET status = 'cancelled', updated_at = NOW() "
                    "WHERE task_id = ANY($1::uuid[])",
                    task_ids,
                )
            else:
                return {"error": "provide task_ids or set all_non_terminal=true"}
            count = int(result.split()[-1])
            return {"cancelled": count}
        finally:
            await conn.close()
    except Exception as e:
        return {"error": str(e)}


async def wipe_tasks_and_plans() -> dict:
    """Truncate all Task Manager tables (tasks, plans, audit_log, events)."""
    try:
        conn = await asyncpg.connect(f"{settings.postgres_dsn}/task_manager")
        try:
            await conn.execute("TRUNCATE audit_log CASCADE")
            await conn.execute("TRUNCATE events CASCADE")
            await conn.execute("TRUNCATE tasks CASCADE")
            await conn.execute("TRUNCATE plans CASCADE")
            return {"wiped": True, "tables": ["tasks", "plans", "audit_log", "events"]}
        finally:
            await conn.close()
    except Exception as e:
        return {"error": str(e)}


async def wipe_memory(memory_records: bool = True, task_outcomes: bool = True) -> dict:
    """Wipe State DB memory records and/or task outcomes."""
    wiped = []
    try:
        conn = await asyncpg.connect(f"{settings.postgres_dsn}/state_db")
        try:
            if memory_records:
                await conn.execute("TRUNCATE memory_records CASCADE")
                wiped.append("memory_records")
            if task_outcomes:
                await conn.execute("TRUNCATE task_outcomes CASCADE")
                wiped.append("task_outcomes")
        finally:
            await conn.close()
        return {"wiped": wiped}
    except Exception as e:
        return {"error": str(e)}


async def get_service_health() -> dict:
    """Check health of all ecosystem services."""
    results = {}
    async def check(name: str, url: str) -> tuple[str, dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{url}/health")
                return name, {"status": "ok" if r.is_success else "error", "code": r.status_code}
        except Exception as e:
            return name, {"status": "unreachable", "error": str(e)}

    checks = await asyncio.gather(*[check(n, u) for n, u in settings.ecosystem_services.items()])
    return dict(checks)


async def get_memory(query: str = "", limit: int = 10) -> dict:
    """Search State DB memory records."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            params = {"top_k": limit}
            if query:
                params["query"] = query
                r = await client.get(f"{settings.state_db_url}/memory/search", params=params)
            else:
                r = await client.get(f"{settings.state_db_url}/memory", params={"limit": limit})
            r.raise_for_status()
            return {"records": r.json()}
    except Exception as e:
        return {"error": str(e)}


async def run_sql(database: str, sql: str) -> dict:
    """Run a SQL query directly against task_manager or state_db Postgres."""
    if database not in ("task_manager", "state_db"):
        return {"error": "database must be task_manager or state_db"}
    try:
        conn = await asyncpg.connect(f"{settings.postgres_dsn}/{database}")
        try:
            rows = await conn.fetch(sql)
            return {"rows": [dict(r) for r in rows], "count": len(rows)}
        finally:
            await conn.close()
    except Exception as e:
        return {"error": str(e)}


async def inject_to_brain(message: str, context: str = "", recipient: str = "user:admin") -> dict:
    """Inject a stimulus directly to the Stimulus Bus with a tag that forces Brain routing."""
    # Prefix with [BRAIN] so the Stimulus Bus router skips classification and routes to Brain
    full_message = f"[BRAIN] {message}"
    if context:
        full_message += f"\n\nContext from Steward: {context}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{settings.stimulus_bus_url}/adapters/internal/stimuli",
                json={
                    "source": "internal",
                    "actor": {"id": recipient, "display_name": "Steward", "type": "system"},
                    "content": {"type": "text", "text": full_message},
                },
                headers={"X-Service-Token": "steward-internal"},
            )
            r.raise_for_status()
            return {"injected": True, "stimulus_id": r.json().get("stimulus_id")}
    except Exception as e:
        return {"error": str(e)}


TOOL_MAP = {
    "get_tasks": get_tasks,
    "get_plan": get_plan,
    "cancel_tasks": cancel_tasks,
    "wipe_tasks_and_plans": wipe_tasks_and_plans,
    "wipe_memory": wipe_memory,
    "get_service_health": get_service_health,
    "get_memory": get_memory,
    "run_sql": run_sql,
    "inject_to_brain": inject_to_brain,
}

TOOL_DEFINITIONS = [
    {
        "name": "get_tasks",
        "description": "Get tasks from Task Manager, optionally filtered by status (running, ready, pending, completed, failed, cancelled).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status. Omit to get all."},
                "limit": {"type": "integer", "description": "Max tasks to return. Default 50."},
            },
        },
    },
    {
        "name": "get_plan",
        "description": "Get a specific plan and all its tasks by plan_id.",
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
        },
    },
    {
        "name": "cancel_tasks",
        "description": "Cancel tasks directly in the database. Use task_ids to cancel specific tasks, or all_non_terminal=true to cancel everything not yet completed/failed/cancelled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_ids": {"type": "array", "items": {"type": "string"}},
                "all_non_terminal": {"type": "boolean"},
            },
        },
    },
    {
        "name": "wipe_tasks_and_plans",
        "description": "Completely wipe all tasks, plans, audit logs, and events from Task Manager. Use when user asks to start fresh or clear everything.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wipe_memory",
        "description": "Wipe State DB memory records and/or task outcomes. Use when user asks to clear Brain memory or start fresh.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_records": {"type": "boolean", "description": "Wipe memory_records table. Default true."},
                "task_outcomes": {"type": "boolean", "description": "Wipe task_outcomes table. Default true."},
            },
        },
    },
    {
        "name": "get_service_health",
        "description": "Check health of all 14 ecosystem services in parallel. Returns status for each.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_memory",
        "description": "Search or list State DB memory records.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query. Omit to list recent."},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "run_sql",
        "description": "Run a SQL query directly against task_manager or state_db Postgres. Use for anything the other tools don't cover.",
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {"type": "string", "enum": ["task_manager", "state_db"]},
                "sql": {"type": "string"},
            },
            "required": ["database", "sql"],
        },
    },
    {
        "name": "inject_to_brain",
        "description": "Escalate to the Brain. Use ONLY when the user wants actual work done that requires code changes, deployments, or research — things Steward can't do directly. Pass a clear task description and any context Steward discovered.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The task for Brain to work on."},
                "context": {"type": "string", "description": "Context from Steward's investigation that Brain should know."},
                "recipient": {"type": "string", "description": "User ID to send Brain's response to."},
            },
            "required": ["message"],
        },
    },
]
