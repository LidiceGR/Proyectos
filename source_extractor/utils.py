from typing import Any, Callable


EventCallback = Callable[[dict[str, Any]], None]

def _emitEvent(callback: EventCallback | None, event: str, **details: Any) -> None:
    """Send a structured progress message when a callback is supplied."""
    if callback is not None:
        callback({"event": event, **details})


def _print_event(event: dict[str, Any]) -> None:
    print(
        f"[{event['event']}] " +
        " ".join(
            f"{key}={value}"
            for key, value in event.items()
            if key != "event"
        ),
        flush=True
    )