"""
Pre-flight system snapshot fetched before the LLM session.
Gives Claude immediate, rich context without needing tool calls for basics.
"""
import asyncio
import json
import httpx
from .config import settings


async def build_snapshot() -> str:
    """Fetch task state and service health concurrently. Returns plain-text summary."""
    results = await asyncio.gather(
        _get_task_details(),
        _get_service_summary(),
        return_exceptions=True,
    )
    task_summary = results[0] if not isinstance(results[0], Exception) else "Task Manager unavailable."
    service_summary = results[1] if not isinstance(results[1], Exception) else "Service health unavailable."
    return f"TASKS:\n{task_summary}\n\nSERVICE HEALTH:\n{service_summary}"


async def _get_task_details() -> str:
    """Fetch non-terminal tasks with descriptions so Claude knows exactly what's running."""
    lines = []
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for status in ("running", "ready", "pending"):
                r = await client.get(
                    f"{settings.task_manager_url}/tasks",
                    params={"status": status, "limit": 20},
                )
                if not r.is_success:
                    continue
                tasks = r.json()
                for t in tasks:
                    tid = t.get("task_id", "")[:8]
                    cap = t.get("capability_id", "?")
                    desc = str(t.get("description") or "")[:120]
                    plan = str(t.get("plan_id") or "")[:8]
                    retry = t.get("retry_count", 0)
                    lines.append(f"  [{status}] {cap} | task={tid} plan={plan} retry={retry} | {desc}")
    except Exception as e:
        return f"  Error fetching tasks: {e}"
    return "\n".join(lines) if lines else "  All queues empty — nothing running or pending."


async def _get_service_summary() -> str:
    """Hit /health on all services in parallel."""
    down = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            checks = await asyncio.gather(
                *[
                    asyncio.wait_for(client.get(f"{url}/health"), timeout=3.0)
                    for url in settings.ecosystem_services.values()
                ],
                return_exceptions=True,
            )
            for name, result in zip(settings.ecosystem_services.keys(), checks):
                if isinstance(result, Exception):
                    down.append(f"{name} (unreachable)")
                elif not result.is_success:
                    down.append(f"{name} (HTTP {result.status_code})")
    except Exception:
        return "  Unavailable."
    if down:
        return "  Down: " + ", ".join(down)
    return "  All services healthy."
