"""
API integration tests using FastAPI TestClient with an in-memory SQLite database.
Tests the full request/response cycle for auth and task management.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app

# ---------------------------------------------------------------------------
# SQLite in-memory test database setup
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite://"

engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# SQLite doesn't have UUID or JSONB natively; we need to patch the models
# to use String/Text for those. For testing, we use a simpler approach:
# we emit the schema differently.

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    """Create tables in the in-memory SQLite DB before tests."""
    # SQLite-compatible schema creation
    # We need to handle Postgres-specific types
    from sqlalchemy.dialects import sqlite
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_register_user(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "password": "securepass123",
            "display_name": "Test User",
            "timezone": "Asia/Seoul",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert data["display_name"] == "Test User"
        assert "id" in data

    def test_register_duplicate_email(self, client):
        client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "securepass123",
            "display_name": "Dup User",
            "timezone": "UTC",
        })
        resp = client.post("/api/v1/auth/register", json={
            "email": "dup@example.com",
            "password": "anotherpass",
            "display_name": "Dup User 2",
            "timezone": "UTC",
        })
        assert resp.status_code == 409

    def test_login_success(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "securepass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_me_endpoint(self, client):
        login_resp = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "securepass123",
        })
        token = login_resp.json()["access_token"]

        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@example.com"

    def test_me_unauthenticated(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code in (401, 403)  # HTTPBearer returns 401 when no auth header


# ---------------------------------------------------------------------------
# Goals tests
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_headers(client):
    """Register a fresh user and return auth headers."""
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "testpass123",
        "display_name": "Test",
        "timezone": "UTC",
    })
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestGoals:
    def test_create_goal(self, client, auth_headers):
        resp = client.post("/api/v1/goals", json={
            "name": "Learn Korean",
            "description": "Daily language study",
            "category": "language",
            "weekly_quota_minutes": 300,
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Learn Korean"
        assert data["weekly_quota_minutes"] == 300
        assert data["is_active"] is True

    def test_list_goals(self, client, auth_headers):
        client.post("/api/v1/goals", json={"name": "Goal 1"}, headers=auth_headers)
        client.post("/api/v1/goals", json={"name": "Goal 2"}, headers=auth_headers)
        resp = client.get("/api/v1/goals", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_get_goal(self, client, auth_headers):
        create_resp = client.post("/api/v1/goals", json={"name": "Get Me"}, headers=auth_headers)
        goal_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/goals/{goal_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == goal_id

    def test_get_goal_not_found(self, client, auth_headers):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/goals/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    def test_update_goal(self, client, auth_headers):
        create_resp = client.post("/api/v1/goals", json={"name": "Update Me"}, headers=auth_headers)
        goal_id = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/goals/{goal_id}", json={"name": "Updated Name"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"

    def test_delete_goal(self, client, auth_headers):
        create_resp = client.post("/api/v1/goals", json={"name": "Delete Me"}, headers=auth_headers)
        goal_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/goals/{goal_id}", headers=auth_headers)
        assert resp.status_code == 204
        # Verify gone
        get_resp = client.get(f"/api/v1/goals/{goal_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_goals_isolated_by_user(self, client):
        """User A cannot see User B's goals."""
        email_a = f"a_{uuid.uuid4().hex[:6]}@example.com"
        email_b = f"b_{uuid.uuid4().hex[:6]}@example.com"

        client.post("/api/v1/auth/register", json={"email": email_a, "password": "pass1234x", "display_name": "A", "timezone": "UTC"})
        client.post("/api/v1/auth/register", json={"email": email_b, "password": "pass1234x", "display_name": "B", "timezone": "UTC"})

        token_a = client.post("/api/v1/auth/login", json={"email": email_a, "password": "pass1234x"}).json()["access_token"]
        token_b = client.post("/api/v1/auth/login", json={"email": email_b, "password": "pass1234x"}).json()["access_token"]

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        create_resp = client.post("/api/v1/goals", json={"name": "User A Goal"}, headers=headers_a)
        goal_id = create_resp.json()["id"]

        # User B tries to access User A's goal
        resp = client.get(f"/api/v1/goals/{goal_id}", headers=headers_b)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task Template tests
# ---------------------------------------------------------------------------

class TestTaskTemplates:
    def test_create_template(self, client, auth_headers):
        resp = client.post("/api/v1/task-templates", json={
            "name": "Morning Workout",
            "scheduling_class": "fixed_recurring",
            "duration_minutes": 60,
            "priority": 80,
            "is_recurring": True,
            "recurrence_frequency": "daily",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Morning Workout"
        assert data["scheduling_class"] == "fixed_recurring"

    def test_list_templates_by_category(self, client, auth_headers):
        client.post("/api/v1/task-templates", json={
            "name": "Korean Study",
            "scheduling_class": "quota_based",
            "duration_minutes": 45,
            "category": "language",
        }, headers=auth_headers)
        resp = client.get("/api/v1/task-templates?category=language", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["category"] == "language" for t in data)

    def test_update_template(self, client, auth_headers):
        create = client.post("/api/v1/task-templates", json={
            "name": "Old Name",
            "scheduling_class": "opportunistic",
            "duration_minutes": 30,
        }, headers=auth_headers)
        tid = create.json()["id"]
        resp = client.patch(f"/api/v1/task-templates/{tid}", json={"name": "New Name"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_openapi_schema_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        # Check key endpoints are documented
        assert "/api/v1/auth/register" in schema["paths"]
        assert "/api/v1/schedules/generate" in schema["paths"]
        assert "/api/v1/audit" in schema["paths"]
