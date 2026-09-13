from __future__ import annotations

import json
import logging

from app.observability.context import accept_request_id, bind_request, clear_request
from app.observability.logging import JsonFormatter


def test_accept_request_id_keeps_valid_ids() -> None:
    assert accept_request_id("  abc-123_XYZ.9:0 ") == "abc-123_XYZ.9:0"


def test_accept_request_id_replaces_short_or_unsafe_ids() -> None:
    assert accept_request_id("short") != "short"
    assert accept_request_id("has space 12345") != "has space 12345"
    assert accept_request_id(None)


def test_json_formatter_includes_request_id_and_extra_fields() -> None:
    token = bind_request("trace-json-0001")
    try:
        record = logging.LogRecord(
            "app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None
        )
        record.event = "unit_test"
        payload = json.loads(JsonFormatter("argumind-test").format(record))
    finally:
        clear_request(token)
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "trace-json-0001"
    assert payload["event"] == "unit_test"
    assert payload["service"] == "argumind-test"
