# Onboarding Guide

Welcome to **Life Scheduler**! This guide will get you up and running whether you want to **use** the application or **develop** it further.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Using the Application](#using-the-application)
   - [Quick Start with Docker](#quick-start-with-docker)
   - [Verify the Installation](#verify-the-installation)
   - [Your First Schedule](#your-first-schedule)
3. [Developing the Application](#developing-the-application)
   - [Development Prerequisites](#development-prerequisites)
   - [Clone and Set Up the Repository](#clone-and-set-up-the-repository)
   - [Configure Environment Variables](#configure-environment-variables)
   - [Run Database Migrations](#run-database-migrations)
   - [Start the Development Server](#start-the-development-server)
   - [Run the Tests](#run-the-tests)
   - [Project Structure](#project-structure)
   - [Making Your First Contribution](#making-your-first-contribution)
4. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### For Users (Docker-based setup)

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| [Docker](https://docs.docker.com/get-docker/) | 24+ | Docker Desktop includes Compose |
| [Docker Compose](https://docs.docker.com/compose/install/) | v2.x | Usually bundled with Docker Desktop |
| [Git](https://git-scm.com/downloads) | any | For cloning the repository |

### For Developers (local setup)

Everything above, plus:

| Tool | Minimum Version | Notes |
|------|----------------|-------|
| [Python](https://www.python.org/downloads/) | 3.11+ | Check with `python --version` |
| [PostgreSQL](https://www.postgresql.org/download/) | 15+ | Or use the Docker Compose `db` service |

---

## Using the Application

### Quick Start with Docker

This is the recommended way to run the application if you just want to use it.

```bash
# 1. Clone the repository
git clone https://github.com/Alegruz/Scheduler.git
cd Scheduler

# 2. Start all services (PostgreSQL + FastAPI backend)
docker-compose up -d

# 3. Wait ~10 seconds for the database to initialise, then open the API docs
open http://localhost:8000/docs   # macOS
xdg-open http://localhost:8000/docs  # Linux
# Windows: navigate to http://localhost:8000/docs in your browser
```

> **Tip:** The first `docker-compose up` builds the backend image and runs the database migrations automatically. Subsequent starts are much faster.

To stop all services:

```bash
docker-compose down
```

To stop and remove all data (including the database volume):

```bash
docker-compose down -v
```

### Verify the Installation

Once the services are up, confirm everything is healthy:

```bash
# Should print the API title and version
curl http://localhost:8000/
```

Expected response:

```json
{"message": "Life Scheduler API"}
```

The interactive Swagger UI is available at `http://localhost:8000/docs`.

### Your First Schedule

Use the Swagger UI (`http://localhost:8000/docs`) or `curl` to walk through the core workflow.

#### 1. Register a user account

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "changeme", "full_name": "Your Name", "timezone": "UTC"}'
```

#### 2. Log in and get an access token

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=you@example.com&password=changeme"
```

Copy the `access_token` value from the response and export it:

```bash
export TOKEN="<paste your token here>"
```

#### 3. Create a task template

```bash
curl -X POST http://localhost:8000/api/v1/task-templates \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Morning Run",
    "duration_minutes": 30,
    "scheduling_class": "fixed_recurring",
    "preferred_start_time": "07:00",
    "priority": 2
  }'
```

#### 4. Generate a proposed schedule for today

```bash
curl -X POST http://localhost:8000/api/v1/schedules/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### 5. Review and commit the schedule

```bash
# Replace <plan_id> with the id returned in the previous step
curl -X POST http://localhost:8000/api/v1/schedules/commit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "<plan_id>"}'
```

#### 6. View today's committed schedule

```bash
curl http://localhost:8000/api/v1/schedules/today \
  -H "Authorization: Bearer $TOKEN"
```

Refer to the [API Reference in README.md](README.md#api-reference) for the full list of available endpoints.

---

## Developing the Application

### Development Prerequisites

Install [Python 3.11+](https://www.python.org/downloads/) and verify:

```bash
python --version   # Python 3.11.x or higher
```

You also need a running PostgreSQL 15+ instance. The easiest way is to start only the database service from Docker Compose:

```bash
docker-compose up -d db
```

### Clone and Set Up the Repository

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/<your-username>/Scheduler.git
cd Scheduler

# 2. Add the upstream remote so you can pull future changes
git remote add upstream https://github.com/Alegruz/Scheduler.git

# 3. Create and activate a virtual environment inside the backend directory
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 4. Install the package and all development dependencies
pip install -e ".[dev]"
```

### Configure Environment Variables

```bash
# Copy the example env file and open it in your editor
cp .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Example value | Notes |
|----------|---------------|-------|
| `DATABASE_URL` | `postgresql://scheduler:scheduler@localhost:5432/scheduler` | Must match your PostgreSQL setup |
| `SECRET_KEY` | `$(python -c "import secrets; print(secrets.token_hex(32))")` | Generate a long random string |

All other variables are optional for local development (see [Environment Variables in README.md](README.md#key-environment-variables)).

### Run Database Migrations

With the database running and `.env` configured:

```bash
# From backend/
alembic upgrade head
```

You should see output similar to:

```
INFO  [alembic.runtime.migration] Running upgrade  -> <revision>, initial schema
```

### Start the Development Server

```bash
# From backend/ (with .venv active)
uvicorn app.main:app --reload --port 8000
```

The `--reload` flag restarts the server automatically whenever you save a Python file.

Open `http://localhost:8000/docs` to confirm the server is running.

### Run the Tests

The test suite uses **pytest** and an in-memory SQLite database so no running PostgreSQL is needed:

```bash
# From backend/ (with .venv active)
python -m pytest tests/ -v                           # all 52 tests
python -m pytest tests/unit/test_scheduler.py -v    # 34 scheduler unit tests only
python -m pytest tests/test_api.py -v               # 18 API integration tests only
python -m pytest tests/ --cov=app                   # with coverage report
```

All tests should pass before you open a pull request.

### Project Structure

```
Scheduler/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/   # Route handlers (auth, goals, schedules, audit, …)
│   │   ├── core/               # Config loading, JWT security helpers
│   │   ├── db/                 # SQLAlchemy models and session factory
│   │   ├── engine/             # Deterministic scheduling engine ← start here
│   │   ├── jobs/               # APScheduler background workers
│   │   └── schemas/            # Pydantic v2 request/response schemas
│   ├── alembic/versions/       # Database migration scripts
│   ├── tests/
│   │   ├── unit/               # Pure Python unit tests (no DB needed)
│   │   └── test_api.py         # FastAPI integration tests (SQLite in-memory)
│   ├── .env.example            # Template for environment variables
│   ├── Dockerfile
│   └── pyproject.toml
├── docker-compose.yml
├── CONTRIBUTING.md             # Git workflow, branching, commit conventions
├── ONBOARDING.md               # ← you are here
└── README.md                   # Full product & architecture documentation
```

**Where to start reading the code:**

| You want to understand… | Start in… |
|-------------------------|-----------|
| Scheduling algorithm | `backend/app/engine/scheduler.py` |
| Database schema | `backend/app/db/models.py` |
| REST API surface | `backend/app/api/v1/endpoints/` |
| Background jobs | `backend/app/jobs/` |
| App configuration | `backend/app/core/config.py` |

### Making Your First Contribution

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the full Git workflow, branching strategy, and commit conventions.
2. Pick an open issue from the [issue tracker](https://github.com/Alegruz/Scheduler/issues) or propose your own.
3. Create a branch off `develop` following the naming convention (e.g. `feature/scheduler-add-buffer-time`).
4. Write or update tests to cover your change.
5. Make sure the full test suite passes: `python -m pytest tests/ -v`.
6. Open a pull request targeting `develop` and fill in the PR template.

---

## Troubleshooting

### `docker-compose up` fails: port 5432 already in use

A local PostgreSQL instance is already running on that port.

**Option A** — Stop the local instance temporarily:

```bash
# macOS (Homebrew)
brew services stop postgresql
# Linux (systemd)
sudo systemctl stop postgresql
```

**Option B** — Change the host port in `docker-compose.yml`:

```yaml
ports:
  - "5433:5432"   # use 5433 on the host
```

Then update `DATABASE_URL` in your `.env` to use port `5433`.

---

### `alembic upgrade head` fails: connection refused

Make sure PostgreSQL is running and that `DATABASE_URL` in `.env` is correct.

```bash
# Quick connectivity test
psql $DATABASE_URL -c "SELECT 1;"
```

If using Docker Compose for the database:

```bash
docker-compose up -d db
# Wait ~5 seconds for the healthcheck to pass
docker-compose ps   # db should show "healthy"
```

---

### `ModuleNotFoundError` when running tests or the server

Make sure your virtual environment is activated and the package is installed in editable mode:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

---

### Server starts but returns `500 Internal Server Error` on every request

Check the server logs for a database connection error. The most common cause is a missing or incorrect `DATABASE_URL`.

```bash
# Print current value
echo $DATABASE_URL   # should not be empty

# Or check your .env file
cat backend/.env
```

---

*Last updated: 2026-03-06. Maintained by [@Alegruz](https://github.com/Alegruz).*
