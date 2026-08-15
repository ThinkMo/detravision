from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class FeishuNotifier:
    def __init__(self, webhook: str | None):
        self.webhook = webhook

    def send(self, event: dict) -> None:
        if not self.webhook:
            return
        body = {
            "msg_type": "text",
            "content": {
                "text": (
                    f"Safety Detect 告警\n摄像头: {event['camera_id']}\n"
                    f"类型: {event['event_type']}\n置信度: {event['confidence']:.2%}"
                )
            },
        }
        request = Request(
            self.webhook,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                if response.status >= 300:
                    logger.error("Feishu webhook returned status %s", response.status)
        except Exception:
            logger.exception("Failed to send Feishu notification")
