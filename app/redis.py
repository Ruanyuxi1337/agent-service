import time
import logging
from typing import AsyncGenerator, Optional
import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger("agent_service")


class RedisManager:
    def __init__(self):
        self.pool: Optional[aioredis.ConnectionPool] = None
        self.client: Optional[aioredis.Redis] = None

    def initialize(self):
        """Initialize connection pool."""
        logger.info(f"Connecting to Redis at {settings.REDIS_URL}...")
        self.pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL, decode_responses=True, max_connections=50
        )
        self.client = aioredis.Redis(connection_pool=self.pool)
        logger.info("Redis connected successfully.")

    async def close(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.disconnect()
            logger.info("Redis connection closed.")

    async def get_client(self) -> aioredis.Redis:
        if self.client is None:
            self.initialize()
        assert self.client is not None
        return self.client


redis_manager = RedisManager()


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    client = await redis_manager.get_client()
    yield client


class DistributedLock:
    """Simple distributed lock using Redis SET NX PX."""

    def __init__(self, key: str, ttl_ms: int = 10000):
        self.key = f"lock:{key}"
        self.ttl_ms = ttl_ms
        self.token = str(time.time())
        self.redis: Optional[aioredis.Redis] = None

    async def acquire(self) -> bool:
        self.redis = await redis_manager.get_client()
        # NX: set if not exists, PX: set expiration in milliseconds
        res = await self.redis.set(self.key, self.token, nx=True, px=self.ttl_ms)
        return bool(res)

    async def release(self) -> None:
        if self.redis is None:
            return
        # Use Lua script to safely release the lock only if the token matches (avoid releasing someone else's lock)
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(lua_script, 1, self.key, self.token)


async def check_rate_limit(client_id: str, limit: int = 60, period: int = 60) -> bool:
    """
    Sliding window or simple bucket rate limiter in Redis.
    Returns True if allowed, False if limited.
    """
    r = await redis_manager.get_client()
    key = f"ratelimit:{client_id}"
    current_time = int(time.time())

    # Use transactional pipeline to clean old and add new request
    async with r.pipeline(transaction=True) as pipe:
        # Clear members older than period
        pipe.zremrangebyscore(key, 0, current_time - period)
        # Count requests in window
        pipe.zcard(key)
        # Add current request
        pipe.zadd(key, {str(current_time) + "_" + str(time.time()): current_time})
        # Set TTL on key for cleanup
        pipe.expire(key, period)
        _, count, _, _ = await pipe.execute()

    return count <= limit


async def is_duplicate_request(idempotency_key: str, ttl_seconds: int = 300) -> bool:
    """
    Check if a request is duplicate using an idempotency key.
    Returns True if duplicate, False if new.
    """
    if not idempotency_key:
        return False
    r = await redis_manager.get_client()
    key = f"idempotency:{idempotency_key}"
    # setnx returns 1 if key was set, 0 if already existed
    is_new = await r.set(key, "1", ex=ttl_seconds, nx=True)
    return not is_new
