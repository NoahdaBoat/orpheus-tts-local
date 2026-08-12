"""Static catalogs used by the web UI and API."""

MESSAGE_SOUNDS: dict[str, list[dict[str, str]]] = {
    "incoming": [
        {"id": "incoming1", "label": "Incoming 1", "file": "incoming1.mp3"},
        {"id": "incoming2", "label": "Incoming 2", "file": "incoming2.mp3"},
        {"id": "incoming3", "label": "Incoming 3", "file": "incoming3.mp3"},
    ],
    "outgoing": [
        {"id": "outgoing1", "label": "Outgoing 1", "file": "outgoing1.mp3"},
    ],
}
