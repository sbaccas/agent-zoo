"""Pytest-wide setup.

Isolates the cat agent's on-disk runtime from any live workbench server.

`actors.cat.store` resolves its transcript and coaching-note paths once, at
import time, from a module-level constant. A test run and a running
`actors.cat.api` server therefore land on the same two files: the suite's
fixtures truncate the transcript the live agent is mid-way through writing, and
the live agent appends real ticks into the middle of a test's expectations.

The override has to be set before `actors.cat.store` is first imported, which is
why it lives here rather than in a fixture -- conftest is imported ahead of the
test modules.
"""

import os
import tempfile
from pathlib import Path

_RUNTIME_DIR = Path(tempfile.gettempdir()) / "cat_agent_test_runtime"
_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
os.environ["CAT_AGENT_RUNTIME_DIR"] = str(_RUNTIME_DIR)

# Keep the workbench lifespan from dialling the live game server on :8000 during
# tests, which would inject real observations into the agent state under test.
os.environ.setdefault("CAT_AGENT_AUTO_CONNECT", "0")
