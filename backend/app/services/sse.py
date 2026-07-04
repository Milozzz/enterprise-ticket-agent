from __future__ import annotations

import json
from typing import Any


def encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def map_ui_type(ui_type: str) -> str:
    if ui_type == "thinking_stream":
        return "AgentThinkingStream"
    return "".join(word.capitalize() for word in ui_type.split("_"))


def ui_event_payload(ui_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": map_ui_type(str(ui_event.get("type", ""))),
        "props": ui_event.get("data", {}),
    }
