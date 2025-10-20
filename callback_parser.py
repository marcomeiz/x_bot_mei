from dataclasses import dataclass
from enum import Enum


class CallbackType(Enum):
    APPROVE = "approve"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    REJECT = "reject"
    GENERATE = "generate"
    NOOP = "noop"
    UNKNOWN = "unknown"


@dataclass
class CallbackAction:
    type: CallbackType
    topic_id: str = ""
    option: str = ""
    raw: str = ""


def parse_callback(data: str) -> CallbackAction:
    if not data:
        return CallbackAction(CallbackType.UNKNOWN, raw=data)

    parts = data.split("_")
    prefix = parts[0]

    if prefix == "approve" and len(parts) >= 3:
        option = parts[1]
        topic_id = parts[2]
        return CallbackAction(CallbackType.APPROVE, topic_id=topic_id, option=option, raw=data)

    if prefix == "confirm" and len(parts) >= 3:
        option = parts[1]
        topic_id = parts[2]
        return CallbackAction(CallbackType.CONFIRM, topic_id=topic_id, option=option, raw=data)

    if prefix == "reject" and len(parts) >= 2:
        topic_id = parts[1]
        return CallbackAction(CallbackType.REJECT, topic_id=topic_id, raw=data)

    if prefix == "cancel":
        topic_id = parts[1] if len(parts) >= 2 else ""
        return CallbackAction(CallbackType.CANCEL, topic_id=topic_id, raw=data)

    if prefix == "noop":
        topic_id = parts[1] if len(parts) >= 2 else ""
        return CallbackAction(CallbackType.NOOP, topic_id=topic_id, raw=data)

    if prefix.startswith("generate") or data.startswith("generate"):
        topic_id = parts[1] if len(parts) >= 2 else ""
        return CallbackAction(CallbackType.GENERATE, topic_id=topic_id, raw=data)

    return CallbackAction(CallbackType.UNKNOWN, raw=data)
