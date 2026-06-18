FROM python:3.13-slim@sha256:3a2c25932e66f706172de831a1b283d491c53ef876cd7fc55a62bcf9a6dd2c61

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

#  --- Stage 1: Cache Deps ---

# copy over the root pyproject.toml, uv.lock and the backend pyproject.toml
COPY pyproject.toml uv.lock ./
COPY apps/backend/pyproject.toml apps/backend/
COPY apps/frontend/pyproject.toml apps/frontend/

# install deps
RUN uv sync --package backend --no-install-project --no-dev

# --- Stage 2: Source Code ---
# copy files over
COPY apps/backend/ apps/backend

# install
RUN uv sync --package backend --no-dev


# --- Stage 3. Execution ---
# create non-root user with home directory and system account
RUN useradd -m -r appuser
RUN chown -R appuser:appuser /app

# switch to user
USER appuser

# put uv venv on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run uvicorn directly from venv (avoids uv re-syncing at runtime)
CMD ["/app/.venv/bin/uvicorn", "backend.main:app", "--app-dir", "apps/backend/src", "--host", "0.0.0.0", "--port", "8000"]
