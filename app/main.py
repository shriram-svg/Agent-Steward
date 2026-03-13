import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import settings
from .hiloop_client import send_response
from .session import run_session
from .snapshot import build_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


class StimulusPayload(BaseModel):
    stimulus_id: str = ""
    actor: dict = {}
    content: dict = {}
    context: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Steward started")
    yield
    logger.info("Steward stopped")


app = FastAPI(title="Steward", version="1.0.0", lifespan=lifespan)


@app.post("/stimuli", status_code=202)
async def receive_stimulus(payload: StimulusPayload):
    """
    Receive a stimulus from the Stimulus Bus.
    Runs a Claude session with tool use and responds via HiLoop.
    """
    import asyncio
    asyncio.create_task(_handle(payload))
    return {"status": "accepted", "stimulus_id": payload.stimulus_id}


async def _handle(payload: StimulusPayload) -> None:
    stimulus_id = payload.stimulus_id or str(uuid.uuid4())
    actor = payload.actor
    recipient = actor.get("user_id") or actor.get("id") or "user:admin"
    text = (payload.content.get("text") or "").strip()

    if not text:
        logger.warning(f"Empty stimulus {stimulus_id}, skipping")
        return

    logger.info(f"Steward handling stimulus {stimulus_id}: {text[:100]}")

    try:
        snapshot = await build_snapshot()
        response_text = await run_session(text, recipient, snapshot)
        await send_response(recipient=recipient, body=response_text, title="Steward")
    except Exception as e:
        logger.error(f"Steward session failed for {stimulus_id}: {e}")
        await send_response(
            recipient=recipient,
            body=f"Steward encountered an error: {e}",
            title="Steward",
            severity="warning",
        )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "steward"}
