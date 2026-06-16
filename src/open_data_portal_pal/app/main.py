import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from langsmith import traceable
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from open_data_portal_pal.app.agent import ProductionAgent
from open_data_portal_pal.app.cache import ResponseCache
from open_data_portal_pal.app.config import get_settings
from open_data_portal_pal.app.models import ChatRequest, ChatResponse, ErrorResponse, HealthResponse, MetricResponse
from open_data_portal_pal.app.monitoring import MetricCollector, RequestTimer, get_logger
from open_data_portal_pal.app.security import SecurityPipeline

load_dotenv()
