"""Bounded discriminated incoming wire messages."""

import json

import pytest

from pysx.forms import PayloadError
from pysx.wire import parse_message


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "null",
        "1",
        "true",
        "[]",
        '"event"',
        '{"t":"unknown"}',
        '{"t":"event","h":1}',
        '{"t":"event","h":"h0","rev":true}',
        '{"t":"event","h":"h0","nav_rev":-1}',
        '{"t":"event","h":"h0","event":[]}',
        '{"t":"event","h":"h0","navigation":1}',
        '{"t":"event","h":"h0","extra":0}',
        '{"t":"event","h":"h0","edits":null}',
        '{"t":"adopt"}',
        '{"t":"mounted","ids":[1]}',
        '{"t":"mounted","ids":[],"ops":[]}',
        '{"t":"hello","version":2}',
        '{"t":"hello","version":true}',
        '{"t":"location","url":"/","rev":null}',
        '{"t":"dom_reply","version":1,"id":"x","value":NaN}',
    ],
)
def test_shape_protocol_rejects_malformed_frames(raw: str) -> None:
    with pytest.raises(PayloadError):
        parse_message(raw)


def test_shape_protocol_envelope_size_depth_and_discriminants() -> None:
    with pytest.raises(PayloadError, match="size"):
        parse_message(json.dumps({"t": "event", "h": "h0", "v": "x" * 65536}))
    with pytest.raises(PayloadError):
        parse_message('{"t":"event","h":"h0","v":' + "[" * 20 + "0" + "]" * 20 + "}")
    with pytest.raises(PayloadError, match="version"):
        parse_message('{"t":"event","h":"h0"}', modern=True)
    valid = {"t": "event", "h": "h0", "version": 3, "epoch": "e", "state_rev": 0, "seq": 1}
    assert parse_message(json.dumps(valid), modern=True) == valid
    assert parse_message('{"t":"hello","version":3,"edits":[]}')["t"] == "hello"
    assert parse_message('{"t":"event","h":"h0"}')["h"] == "h0"
    assert (
        parse_message('{"t":"event","h":"","edits":[{"h":"binding","v":"reset","rev":1}]}')["h"]
        == ""
    )


@pytest.mark.parametrize(
    "edit",
    [
        {"h": "", "v": "x", "rev": 1},
        {"h": "h0", "rev": 1},
        {"h": "h0", "v": "x", "rev": 2**80},
        {"h": "h0", "v": "x", "rev": 1, "extra": True},
    ],
)
def test_shape_protocol_nested_edits_are_strict(edit: dict[str, object]) -> None:
    with pytest.raises(PayloadError):
        parse_message(json.dumps({"t": "event", "h": "h0", "edits": [edit]}))


def test_shape_protocol_dom_version_is_an_integer() -> None:
    with pytest.raises(PayloadError):
        parse_message('{"t":"dom_reply","version":true,"id":"x","value":null}')
