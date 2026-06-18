from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.agent import ProductionAgent
from backend.cache import ResponseCache
from backend.config import get_settings
from backend.models import ChatRequest, ChatResponse, HealthResponse, MetricsResponse
from backend.monitoring import MetricsCollector, RequestTimer, get_logger
from backend.security import SecurityPipeline

load_dotenv()


# Global instances (initialized in lifespan)
security: SecurityPipeline = None
cache: ResponseCache = None
metrics: MetricsCollector = None
agent: ProductionAgent = None
logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize all components on startup, clean up on shutdown.
    This is the modern FastAPI pattern (replaces @app.on_event)
    """
    global security, cache, metrics, agent

    settings = get_settings()

    logger.info(
        "Starting production portal pal API...",
        extra={
            "extra_data": {
                "environment": settings.app_env,
                "primary_model": settings.primary_model,
                "tracing_enabled": settings.langsmith_tracing,
            }
        },
    )

    # initialize components
    security = SecurityPipeline()
    cache = ResponseCache(ttl_seconds=settings.cache_ttl_seconds)
    metrics = MetricsCollector()
    agent = ProductionAgent()

    logger.info("All components initialized. Ready to server requests.")

    yield  # App is running

    # shutdown
    logger.info("Shutting down...", extra={"extra_data": metrics.summary})


# auth

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(_api_key_header)) -> None:
    if not api_key or api_key != get_settings().portal_pal_api_key.get_secret_value():
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# rate limiter setup

limiter = Limiter(key_func=get_remote_address)

# fastapi app
app = FastAPI(
    title="Portal Pal",
    description="A production-ready chat api with security, caching, and observability.",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter


# exception handlers


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    logger.warning("Rate limit exceeded", extra={"extra_data": {"client_ip": get_remote_address(request)}})

    return JSONResponse(
        status_code=429, content={"error": "Rate limit exceeded", "detail": "too many requests. Please slow down."}
    )


# chat endpoints


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit(get_settings().rate_limit)
async def chat(request: Request, body: ChatRequest):
    """
    Main chat endpoint.

    Flow:
    1. Security check (injection + PII Masking)
    2. cache lookup
    3. LangGraph agent invoke (if cache miss)
    4. Output validation
    5. cache store
    6. return response
    """
    with RequestTimer() as timer:
        security_notes = []

        # step 1: security check
        is_allowed, cleaned_message, notes = security.check_input(body.message)
        security_notes.extend(notes)

        if not is_allowed:
            logger.warning(
                "Request blocked by security",
                extra={
                    "extra_data": {
                        "reason": notes,
                        "thread_id": body.thread_id,
                    }
                },
            )
            metrics.record_request(latency_ms=0, error=True)
            raise HTTPException(status_code=400, detail="Your message was blocked by our security filters.")

        # step 2: cache lookup
        cached_response = cache.get(cleaned_message)
        if cached_response is not None:
            metrics.record_request(latency_ms=0, cache_hit=True)
            logger.info("Cache hit", extra={"extra_data": {"thread_id": body.thread_id}})
            return ChatResponse(
                response=cached_response,
                thread_id=body.thread_id,
                model_used="cache",
                cached=True,
                processing_time_ms=0,
            )

        # step 3: invoake langgraph agent
        try:
            result = agent.invoke(cleaned_message)
        except Exception as e:
            logger.error(
                f"Agent invocation failed: {e}",
                extra={
                    "extra_data": {
                        "thread_id": body.thread_id,
                        "error": str(e),
                    }
                },
            )
            metrics.record_request(latency_ms=0, error=True)
            raise HTTPException(status_code=500, detail="An Error occurred while processing your request.")

        response_text = result["response"]
        model_used = result["model_used"]

        # step 4: output validation
        validated_response, output_warnings = security.check_output(response_text)
        security_notes.extend(output_warnings)

        # step 5: cache store
        cache.set(cleaned_message, validated_response)

        # step 6. log and record metrics
    input_tokens = int(len(cleaned_message.split()) * 1.3)  # TODO: impl tiktoken
    output_tokens = int(len(validated_response.split()) * 1.3)  # TODO: impl tiktoken

    metrics.record_request(
        latency_ms=timer.elapsed_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit=False,
    )

    if security_notes:
        logger.info("Security notes", extra={"extra_data": {"notes": security_notes, "thread_id": body.thread_id}})

    logger.info(
        "Request completed",
        extra={
            "extra_data": {
                "thread_id": body.thread_id,
                "model_used": model_used,
                "latency_ms": round(timer.elapsed_ms, 2),
            }
        },
    )

    return ChatResponse(
        response=validated_response,
        thread_id=body.thread_id,
        model_used=model_used,
        cached=False,
        processing_time_ms=round(timer.elapsed_ms, 2),
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check for Containers/Kubernetes."""
    settings = get_settings()

    checks = {
        "agent": agent is not None,
        "security": security is not None,
        "cache": cache is not None,
    }

    all_healthy = all(checks.values())

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        environment=settings.app_env,
        checks=checks,
    )


@app.get("/metrics", response_model=MetricsResponse, dependencies=[Depends(verify_api_key)])
async def get_metrics():
    """Metrics for monitoring dashbaords."""
    summary = metrics.summary
    return MetricsResponse(**summary)


@app.get("/cache/stats", dependencies=[Depends(verify_api_key)])
async def cache_stats():
    """Cache performance stats."""
    return cache.stats
