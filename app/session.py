"""
Steward session: one LLM call via Model Router → JSON output → Python executes actions.

Flow:
  1. Build rich snapshot (done by caller)
  2. POST to Model Router /execute (non-agentic, claude --print)
  3. Parse JSON response: { response, actions, escalate_to_brain }
  4. Execute actions via tools.py
  5. Return final response text
"""
import json
import logging
import uuid
import httpx
from .config import settings
from . import tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Steward — the third-party diagnostic and intervention service for this AI ecosystem.
You bypass the Brain entirely. You have direct knowledge of all system state from the snapshot below.

YOUR ROLE:
- Diagnose: interpret task/plan state, service health, memory
- Fix directly: cancel tasks, wipe memory — pure system housekeeping
- Escalate to Brain: ONLY when the user wants actual work done (code changes, builds, deployments)

AVAILABLE ACTIONS (include in your "actions" list as needed):
- { "type": "cancel_tasks", "all_non_terminal": true }  — cancel all non-terminal tasks
- { "type": "cancel_tasks", "task_ids": ["uuid", ...] }  — cancel specific tasks
- { "type": "wipe_tasks_and_plans" }  — truncate all tasks and plans
- { "type": "wipe_memory", "memory_records": true, "task_outcomes": true }  — clear State DB memory

ESCALATION: set escalate_to_brain to a clear task description (string) if the user wants real work done.
Otherwise set it to null.

CURRENT SYSTEM STATE:
{snapshot}

Respond with a single valid JSON object (no markdown, no code fences):
{{
  "response": "<plain-text reply to the user — what you found and/or what you did>",
  "actions": [ ... ],
  "escalate_to_brain": "<task for Brain>" or null
}}
"""

FORMAT_CORRECTION = """\
Your previous response was not valid JSON. Respond with ONLY a valid JSON object — no markdown, \
no code fences, just raw JSON with keys: response, actions, escalate_to_brain.\
"""


async def run_session(user_message: str, recipient: str, snapshot: str) -> str:
    """Run one LLM call, execute actions, return user-facing response text."""
    system = SYSTEM_PROMPT.format(snapshot=snapshot)

    raw = await _call_model(system, user_message)
    result = _parse(raw)

    if result is None:
        # One retry with format correction
        raw = await _call_model(system, user_message, correction=raw or "")
        result = _parse(raw)

    if result is None:
        logger.error("Steward: failed to parse LLM output after retry")
        return "Steward could not parse its own response. Check logs."

    # Execute requested actions
    for action in result.get("actions") or []:
        await _execute_action(action)

    # Escalate to Brain if needed
    escalate = (result.get("escalate_to_brain") or "").strip()
    if escalate:
        await tools.inject_to_brain(escalate, recipient=recipient)

    return result.get("response") or "Done."


async def _call_model(system: str, user_message: str, correction: str = "") -> str:
    messages = [{"role": "user", "content": user_message}]
    if correction:
        messages += [
            {"role": "assistant", "content": correction},
            {"role": "user", "content": FORMAT_CORRECTION},
        ]
    payload = {
        "execution_id": str(uuid.uuid4()),
        "model_preference": ["claude"],
        "prompt": {
            "system": system,
            "messages": messages,
        },
        "agentic": False,
        "stream": True,
    }
    chunks = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", f"{settings.model_router_url}/execute", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        try:
                            ev = json.loads(data)
                            if "text" in ev:
                                chunks.append(ev["text"])
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"Model Router call failed: {e}")
        return ""
    return "".join(chunks).strip()


def _parse(raw: str) -> dict | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        return None


async def _execute_action(action: dict) -> None:
    t = action.get("type")
    try:
        if t == "cancel_tasks":
            task_ids = action.get("task_ids")
            all_non_terminal = action.get("all_non_terminal", False)
            result = await tools.cancel_tasks(task_ids=task_ids, all_non_terminal=all_non_terminal)
            logger.info(f"cancel_tasks → {result}")
        elif t == "wipe_tasks_and_plans":
            result = await tools.wipe_tasks_and_plans()
            logger.info(f"wipe_tasks_and_plans → {result}")
        elif t == "wipe_memory":
            result = await tools.wipe_memory(
                memory_records=action.get("memory_records", True),
                task_outcomes=action.get("task_outcomes", True),
            )
            logger.info(f"wipe_memory → {result}")
        else:
            logger.warning(f"Unknown action type: {t}")
    except Exception as e:
        logger.error(f"Action {t} failed: {e}")
