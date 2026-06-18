"""
Shared test configuration.
Sets required env vars before any module imports so pydantic-settings
validation and module-level decorator calls (e.g. @limiter.limit) succeed.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai-key")
os.environ.setdefault("PORTAL_PAL_API_KEY", "test-secret-key-abc123")
os.environ.setdefault("LANGSMITH_API_KEY", "test-langsmith-key")
