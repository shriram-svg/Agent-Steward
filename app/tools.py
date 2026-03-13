"""
Action implementations executed by Steward after the LLM session returns its JSON plan.
"""
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


