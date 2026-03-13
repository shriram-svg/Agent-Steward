import logging
import uuid
import httpx
from .config import settings

logger = logging.getLogger(__name__)

async def send_response(recipient: str, body: str, title: str = "Steward", severity: str = "info") -> None:
    payload = {
        "request_id": str(uuid.uuid4()),
        "type": "alert",
        "severity": severity,
        "title": title,
        "body": body,
        "recipient": recipient,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{settings.hiloop_url}/requests", json=payload)
            r.raise_for_status()
    except Exception as e:
        logger.error(f"HiLoop send failed: {e}")
