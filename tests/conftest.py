"""Shared test fixtures.

Sets env vars BEFORE any app module is imported so unit tests do not block
on attempting to reach a real MongoDB instance during app startup.
"""

import os

os.environ.setdefault("MONGODB_SKIP_STARTUP", "true")
os.environ.setdefault("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "200")
os.environ.setdefault("MONGODB_CONNECT_TIMEOUT_MS", "200")
