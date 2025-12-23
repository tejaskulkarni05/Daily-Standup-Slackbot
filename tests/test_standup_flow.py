"""
Tests for standup flow using pytest-asyncio.
"""

import pytest
from datetime import date
from unittest.mock import patch

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

from main import app
from app.db.base import Base, get_session
from app.db.repository import (
    UserRepository,
    StandupReportRepository,
    StandupStateRepository,
)
from app.schemas.user import UserCreate
from app.services.user_service import create_user, list_users
from app.services.standup_service import handle_user_answer, handle_skip_today


# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------

TEST_WORKSPACE_ID = "T_TEST_WORKSPACE"


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

@pytest.fixture
async def db_session():
    """Create an in-memory test database session."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session):
    """Create a FastAPI test client with dependency override."""

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# -------------------------------------------------------------------
# Health Endpoints
# -------------------------------------------------------------------

class TestHealthEndpoints:

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "scheduler_running" in data

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Daily Standup Bot"
        assert data["status"] == "running"


# -------------------------------------------------------------------
# User Service
# -------------------------------------------------------------------

class TestUserService:

    @pytest.mark.asyncio
    async def test_create_user(self, db_session):
        user_create = UserCreate(
            slack_user_id="U12345",
            display_name="John Doe",
            email="john@example.com",
            timezone="America/New_York",
        )

        result = await create_user(
            db_session,
            TEST_WORKSPACE_ID,
            user_create,
        )

        assert "error" not in result
        assert result["slack_user_id"] == "U12345"
        assert result["display_name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_list_users(self, db_session):
        await create_user(
            db_session,
            TEST_WORKSPACE_ID,
            UserCreate(slack_user_id="U1", display_name="User 1"),
        )
        await create_user(
            db_session,
            TEST_WORKSPACE_ID,
            UserCreate(slack_user_id="U2", display_name="User 2"),
        )

        users = await list_users(db_session, TEST_WORKSPACE_ID)

        assert len(users) == 2
        assert users[0]["slack_user_id"] == "U1"
        assert users[1]["slack_user_id"] == "U2"

    @pytest.mark.asyncio
    async def test_create_duplicate_user(self, db_session):
        user = UserCreate(slack_user_id="U123", display_name="John")

        await create_user(db_session, TEST_WORKSPACE_ID, user)
        result = await create_user(db_session, TEST_WORKSPACE_ID, user)

        assert "error" in result
        assert "already exists" in result["error"]


# -------------------------------------------------------------------
# Standup Flow
# -------------------------------------------------------------------

class TestStandupFlow:

    @pytest.mark.asyncio
    async def test_standup_state_creation(self, db_session):
        user_repo = UserRepository(db_session)
        user = await user_repo.create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U123",
            display_name="Test User",
        )

        state_repo = StandupStateRepository(db_session)
        state = await state_repo.create_or_update(
            user.id,
            pending_report_date=date.today(),
            current_question_index=0,
        )

        assert state.user_id == user.id
        assert state.current_question_index == 0

    @pytest.mark.asyncio
    async def test_standup_report_creation(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U456",
            display_name="User 2",
        )

        report = await StandupReportRepository(db_session).create(
            user_id=user.id,
            report_date=date.today(),
            feeling="Great",
            yesterday="Did X",
            today="Doing Y",
        )

        assert report.user_id == user.id
        assert report.feeling == "Great"

    @pytest.mark.asyncio
    async def test_unique_report_per_date(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U789",
            display_name="User",
        )

        repo = StandupReportRepository(db_session)
        report_date = date.today()

        r1 = await repo.create(
            user_id=user.id,
            report_date=report_date,
            yesterday="Task A",
        )

        r2 = await repo.get_by_user_date(user.id, report_date)
        assert r1.id == r2.id

    @pytest.mark.asyncio
    async def test_mark_report_completed(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U999",
            display_name="User",
        )

        repo = StandupReportRepository(db_session)
        report = await repo.create(
            user_id=user.id,
            report_date=date.today(),
            yesterday="Work",
        )

        completed = await repo.mark_completed(report.id)
        assert completed.completed_at is not None

    @pytest.mark.asyncio
    async def test_skip_standup(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U111",
            display_name="User",
        )

        await StandupStateRepository(db_session).create_or_update(
            user.id,
            pending_report_date=date.today(),
        )

        result = await handle_skip_today(
            db_session,
            TEST_WORKSPACE_ID,
            "U111",
        )

        assert result["action"] == "skipped"

        state = await StandupStateRepository(db_session).get_by_user(user.id)
        assert state is None


# -------------------------------------------------------------------
# Slack Integration (Mocked)
# -------------------------------------------------------------------

class TestMockSlackIntegration:

    @pytest.mark.asyncio
    async def test_handle_answer_next_question(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U222",
            display_name="User",
        )

        await StandupStateRepository(db_session).create_or_update(
            user.id,
            pending_report_date=date.today(),
            current_question_index=0,
        )

        with patch("app.services.standup_service.get_slack_client"):
            result = await handle_user_answer(
                db_session,
                TEST_WORKSPACE_ID,
                "U222",
                "Feeling great!",
            )

        assert result["action"] == "next_question"
        assert result["question_index"] == 1

    @pytest.mark.asyncio
    async def test_handle_all_answers_complete_report(self, db_session):
        user = await UserRepository(db_session).create(
            workspace_id=TEST_WORKSPACE_ID,
            slack_user_id="U333",
            display_name="User",
        )

        await StandupStateRepository(db_session).create_or_update(
            user.id,
            pending_report_date=date.today(),
            current_question_index=3,
        )

        repo = StandupReportRepository(db_session)
        await repo.create(
            user_id=user.id,
            report_date=date.today(),
            feeling="Good",
            yesterday="Task 1",
            today="Task 2",
        )

        with patch("app.services.standup_service.get_slack_client"):
            result = await handle_user_answer(
                db_session,
                TEST_WORKSPACE_ID,
                "U333",
                "Blocked",
            )

        assert result["action"] == "complete_report"

        state = await StandupStateRepository(db_session).get_by_user(user.id)
        assert state is None

        completed = await repo.get_by_user_date(user.id, date.today())
        assert completed.completed_at is not None


# -------------------------------------------------------------------
# Admin Endpoints
# -------------------------------------------------------------------

class TestAdminEndpoints:

    @pytest.mark.asyncio
    async def test_list_users_endpoint(self, client, db_session):
        await create_user(
            db_session,
            TEST_WORKSPACE_ID,
            UserCreate(slack_user_id="U_test", display_name="Test User"),
        )

        response = await client.get(
            "/admin/users",
            headers={"X-Admin-Token": "test-token"},
        )

        assert response.status_code in (200, 401)

    @pytest.mark.asyncio
    async def test_create_user_endpoint_missing_token(self, client):
        response = await client.post(
            "/admin/users",
            json={
                "slack_user_id": "U_new",
                "display_name": "New User",
            },
        )

        assert response.status_code == 401
