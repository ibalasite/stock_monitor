"""LINE Messaging API push adapter."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from urllib import error, request


@dataclass
class LinePushClient:
    channel_access_token: str = field(repr=False)
    to_group_id: str
    endpoint: str = "https://api.line.me/v2/bot/message/push"
    timeout_sec: int = 10

    def send(self, message: str) -> dict:
        text = str(message).strip()
        if not text:
            raise ValueError("message must not be empty")

        payload = {
            "to": self.to_group_id,
            "messages": [
                {
                    "type": "text",
                    "text": text[:5000],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.channel_access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                status = int(getattr(resp, "status", 200))
                response_text = resp.read().decode("utf-8")
        except socket.timeout as exc:
            raise TimeoutError("line api timeout") from exc
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"line api http error: {exc.code} {detail}") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise TimeoutError("line api timeout") from exc
            raise RuntimeError(f"line api request failed: {exc}") from exc

        if status < 200 or status >= 300:
            raise RuntimeError(f"line api non-success status: {status} {response_text}")
        return {"ok": True, "status": status}

