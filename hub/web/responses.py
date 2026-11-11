"""Answers of the JSON API. Errors are HTTP 200 with {"status": "error", "message": ...}; the page shows the message."""


def fail(message) -> dict:
    return {"status": "error", "message": message}
