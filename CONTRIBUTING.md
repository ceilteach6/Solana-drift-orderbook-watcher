# Contributing

Thanks for contributing! A few ground rules:

## Workflow
1. Fork → feature branch (`git checkout -b feature/description`)
2. Commit with a clear message
3. Run the tests: `pytest tests/ -v`
4. Open a Pull Request

## Adding a detector
Most contributions will be new detectors. Steps:
1. Create a file: `src/detector/my_detector.py`
2. Subclass `BaseDetector` and implement `analyze()`
3. Add a test under `tests/` (mock orderbook, no RPC required)
4. Register it in the `_build_detectors()` method of `src/watcher.py`

## Rules
- NEVER commit `.env` or a private key
- Read-only principle: the main branch must not trade / write to the chain
- Every new piece of logic should come with a test
