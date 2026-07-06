import time
import json
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.redis import check_rate_limit
from app.config import settings

logger = logging.getLogger("agent_service")


class AgentServiceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()

        # 1. Client Identifier (IP/API Token etc.)
        client_ip = request.client.host if request.client else "unknown"

        # 2. Rate Limiting check via Redis (Exempt docs and schema endpoints)
        if not request.url.path.startswith(("/docs", "/openapi.json", "/redoc")):
            is_allowed = await check_rate_limit(
                client_id=client_ip,
                limit=settings.RATE_LIMIT_CALLS,
                period=settings.RATE_LIMIT_PERIOD,
            )
            if not is_allowed:
                logger.warning(f"Rate limit exceeded for client {client_ip}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too Many Requests. Rate limit exceeded. Try again later."
                    },
                )

        # 3. Process the request
        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(f"Unhandled system exception: {str(e)}", exc_info=True)
            return JSONResponse(
                status_code=500, content={"detail": f"Internal Server Error: {str(e)}"}
            )

        # 4. Measure execution time and add header
        duration = time.time() - start_time
        response.headers["X-Process-Time"] = f"{duration:.4f}s"

        # 5. Log request in structured JSON format
        logger.info(
            json_log(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": client_ip,
                    "status_code": response.status_code,
                    "duration_seconds": duration,
                }
            )
        )
        return response


def json_log(data: dict) -> str:
    """Format logs as a single-line JSON string for structured logging engines."""
    data["timestamp"] = time.time()
    return json.dumps(data)
