"""
tests/test_orderbook_feed.py

``_to_float`` is the defensive coercion driftpy L2 levels pass through; its own
docstring promises it "won't crash the watcher" on unexpected object shapes.
These tests hold that promise for both the direct-cast and ``.value``-fallback
paths.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _to_float


def test_plain_number_passes_through():
    assert _to_float(5) == 5.0
    assert _to_float("3.5") == 3.5


def test_object_with_numeric_value_attr_uses_fallback():
    assert _to_float(SimpleNamespace(value=42)) == 42.0


def test_object_with_non_numeric_value_attr_degrades_to_zero():
    # e.g. a driftpy level whose .value is itself a nested object/dict/string
    # that can't be cast — must not raise.
    assert _to_float(SimpleNamespace(value={"nested": "object"})) == 0.0
    assert _to_float(SimpleNamespace(value="not-a-number")) == 0.0


def test_object_with_no_value_attr_degrades_to_zero():
    assert _to_float(object()) == 0.0
