"""Runs a Claude tool-use session for the given stimulus."""
import json
import logging
from anthropic import AsyncAnthropic
from .config import settings
from .tools import TOOL_DEFINITIONS, TOOL_MAP
from .snapshot import build_snapshot

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Steward — the third-party diagnostic and intervention service for this AI ecosystem.

You operate independently of the Brain. You have direct access to all system state and can take corrective actions immediately using your tools.

YOUR ROLE:
- Diagnose: read raw task/plan state, service health, memory, logs
- Fix directly: cancel tasks, wipe memory, restart services — anything that is pure system housekeeping
- Escalate to Brain: ONLY when the user wants actual work done that requires code changes, deployments, or new builds

WHAT YOU HANDLE WITHOUT BRAIN:
- Clearing stuck/zombie tasks
- Wiping memory or task history
- Answering "what is running / what happened / is anything stuck"
- Service health checks
- Any read-only diagnostic query

WHAT YOU ESCALATE TO BRAIN (inject_to_brain):
- "Build X", "deploy Y", "fix the code in Z", "add a feature to W"
- Anything that requires a worker to execute

TONE: Direct, plain, honest. No fluff. Tell the user exactly what you found and what you did.

CURRENT SYSTEM SNAPSHOT (fetched before this session):
{snapshot}

ECOSYSTEM INTERNAL URLS (accessible from your tools):
- Task Manager: http://task-manager:8000
- State DB: http://state-db:8003
- HiLoop Gateway: http://hiloop-gateway:8006
- Postgres: postgresql://postgres:postgres@postgres:5432
"""

MAX_TOOL_ROUNDS = 8


async def run_session(user_message: str, recipient: str, snapshot: str) -> str:
    """Run a Claude tool-use session and return the final text response."""
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    system = SYSTEM_PROMPT.format(snapshot=snapshot)
    messages = [{"role": "user", "content": user_message}]

    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=system,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Collect any text content
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "end_turn" or not tool_uses:
            return "\n".join(text_blocks) or "Done."

        # Execute all tool calls
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tool_use in tool_uses:
            fn = TOOL_MAP.get(tool_use.name)
            if fn:
                try:
                    result = await fn(**tool_use.input)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"unknown tool: {tool_use.name}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })
            logger.info(f"Tool {tool_use.name}({tool_use.input}) -> {str(result)[:200]}")

        messages.append({"role": "user", "content": tool_results})

    return "Steward hit the tool round limit. Check logs for details."
