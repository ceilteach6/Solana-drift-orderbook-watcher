"""
tests/test_alert_dispatcher.py

AlertDispatcher.close() drains every sink that queues asynchronous work
(currently only WebhookAlert) before shutdown, so a detection alerted on the
last tick before the process exits isn't silently dropped. See
src/watcher.py's Watcher.start() finally block and main.py's os._exit().
"""

from types import SimpleNamespace

from src.alert.base import Alert, AlertDispatcher


class _RecordingSink(Alert):
    def __init__(self):
        self.closed_with = None

    def deliver(self, detection):
        pass

    def close(self, timeout: float = 5.0) -> None:
        self.closed_with = timeout


class _BrokenCloseSink(Alert):
    def __init__(self):
        pass

    def deliver(self, detection):
        pass

    def close(self, timeout: float = 5.0) -> None:
        raise RuntimeError("simulated close failure")


def test_close_calls_close_on_every_sink_with_the_given_timeout():
    sinks = [_RecordingSink(), _RecordingSink()]
    dispatcher = AlertDispatcher(SimpleNamespace(alert_min_score=0.0), sinks)

    dispatcher.close(timeout=2.5)

    assert all(s.closed_with == 2.5 for s in sinks)


def test_close_isolates_a_sink_that_fails_to_close():
    # One misbehaving sink must not prevent the others from draining.
    good = _RecordingSink()
    dispatcher = AlertDispatcher(
        SimpleNamespace(alert_min_score=0.0), [_BrokenCloseSink(), good]
    )

    dispatcher.close()  # must not raise

    assert good.closed_with == 5.0


def test_base_alert_close_defaults_to_a_noop():
    # A sink that never overrides close() (e.g. ConsoleAlert, which delivers
    # synchronously) must not need to implement one just to be closeable.
    class _SyncSink(Alert):
        def deliver(self, detection):
            pass

    _SyncSink(SimpleNamespace()).close()  # must not raise
