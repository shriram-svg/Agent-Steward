"""Quick pre-flight snapshot of system state injected as context before Claude session."""
import asyncio
import httpx
from .config import settings


async def build_snapshot() -> str:
    """Fetch key system state concurrently. Returns a plain-text summary."""
    results = await asyncio.gather(
        _get_task_counts(),
        _get_service_summary(),
        return_exceptions=True,
    )
    task_summary = results[0] if not isinstance(results[0], Exception) else "Task Manager unavailable."
    service_summary = results[1] if not isinstance(results[1], Exception) else "Service health unavailable."
    return f"TASK QUEUE:\n{task_summary}\n\nSERVICE HEALTH:\n{service_summary}"


async def _get_task_counts() -> str:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.task_manager_url}/health")
            if r.is_success:
                depths = r.json().get("queue_depths", {})
                lines = [f"  {k}: {v}" for k, v in depths.items() if v > 0]
                return "\n".join(lines) if lines else "  All queues empty."
    except Exception:
        pass
    return "  Unavailable."


async def _get_service_summary() -> str:
    down = []
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            tasks = {
                name: client.get(f"{url}/health")
                for name, url in settings.ecosystem_services.items()
            }
            for name, coro in tasks.items():
                try:
                    r = await asyncio.wait_for(coro, timeout=3.0)
                    if not r.is_success:
                        down.append(f"{name} (HTTP {r.status_code})")
                except Exception:
                    down.append(f"{name} (unreachable)")
    except Exception:
        return "  Unavailable."
    if down:
        return "  Down: " + ", ".join(down)
    return "  All services healthy."
