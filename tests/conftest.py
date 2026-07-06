import pytest
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport

# Override Settings for Testing before importing any app modules
from app.config import settings

settings.DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Mock Redis manager
from app.redis import redis_manager


class MockRedisPipeline:
    def __init__(self, store):
        self.store = store
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def zremrangebyscore(self, key, min_val, max_val):
        self.commands.append(("zrem", key))
        return self

    def zcard(self, key):
        self.commands.append(("zcard", key))
        return self

    def zadd(self, key, mapping):
        self.commands.append(("zadd", key, mapping))
        return self

    def expire(self, key, period):
        self.commands.append(("expire", key))
        return self

    async def execute(self):
        return [0, 1, 1, True]


class MockRedisClient:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, nx=False, px=None, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def eval(self, script, numkeys, *args):
        key = args[0]
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    def pipeline(self, transaction=True):
        return MockRedisPipeline(self.store)


class MockRedisManager:
    def __init__(self):
        self.client = MockRedisClient()

    def initialize(self):
        pass

    async def close(self):
        pass

    async def get_client(self):
        return self.client


# Replace real Redis with Mock
mock_redis = MockRedisManager()
import app.redis

app.redis.redis_manager = mock_redis

# Now import the app
from app.main import app as fastapi_app
from app.database import Base


@pytest.fixture(autouse=True)
async def setup_test_db():
    # Enforce SQLite memory database setup
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    # Overwrite the global engine in database module
    import app.database

    app.database.engine = test_engine
    app.database.async_session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    import app.database

    async with app.database.async_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
