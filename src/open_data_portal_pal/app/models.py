"""
API Request and Response Models
Pydantic modeks for input validation and respons structure.
"""

import datetime
from functools import total_ordering

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Incoming chat request."""

    message: str = Field(
        min_length=1,
        max_length=7000,
        description="The user's message to the agent.",
    )
    thread_id: str = Field(default="default", description="Coversation thread ID")


class ChatResponse(BaseModel):
    """Chat response returned to the client."""

    response: str
    thread_id: str
    model_used: str
    cached: bool = False
    processing_time_ms: float
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(tz=datetime.UTC).isoformat())


class HealthResponse(BaseModel):
    """Health check response"""

    status: str = "healthy"
    environment: str
    version: str = "1.0.0"
    checks: dict = {}


class MetricResponse(BaseModel):
    """Metrics endpoint response."""

    total_requests: int
    total_errors: int
    error_rate: str
    avg_latency_ms: float
    cache_hit_rate: str
    total_input_tokens: int
    total_output_tokens: int


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None
    request_id: str | None = None
