# Task Management API

![CI](https://github.com/oleksii-shcherbak/task-management-api/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

A production-ready REST API for managing projects, tasks, and teams. Built with FastAPI and PostgreSQL, deployed on AWS
EC2.

**[Live API](http://16.170.169.48:8000)** —
**[Swagger UI](http://16.170.169.48:8000/docs)** —
**[ReDoc](http://16.170.169.48:8000/redoc)**

---

## Features

- **Authentication** — JWT access tokens (15 min) + rotating refresh tokens (30 days), GitHub OAuth, email verification,
  password reset
- **Projects & teams** — project membership with owner/manager/member roles, invite by user ID, role management
- **Tasks** — priorities, custom statuses (Linear-style with type enforcement), multi-assignee, drag-and-drop reorder,
  activity log
- **Comments & @mentions** — threaded comments, @username mentions with mention inbox
- **File attachments** — upload to local storage (dev) or S3 (prod), MIME validation, per-task attachment list
- **Cursor-based pagination** — all list endpoints use keyset pagination (no offset drift)
- **Redis caching** — project membership and task statuses cached with TTL, invalidated on mutation
- **Rate limiting** — sliding window via Redis sorted sets, `Retry-After` header on 429
- **Background tasks** — ARQ async worker: email notifications (verification, password reset, assignment, mentions,
  invites), due-date reminders
- **Observability** — structured JSON logging (structlog), request ID tracing, Sentry error tracking

---

## Tech Stack

| Layer                | Technology                                                                 |
|----------------------|----------------------------------------------------------------------------|
| Framework            | FastAPI 0.128+                                                             |
| Language             | Python 3.12                                                                |
| Validation           | Pydantic v2                                                                |
| Database             | PostgreSQL 18 (async via [asyncpg](https://github.com/MagicStack/asyncpg)) |
| ORM / migrations     | SQLAlchemy 2.0 (Mapped style) + Alembic                                    |
| Cache / queue broker | Redis 8                                                                    |
| Background tasks     | [ARQ](https://github.com/samuelcolvin/arq)                                 |
| Authentication       | PyJWT + bcrypt                                                             |
| File storage         | Local (dev) / AWS S3 (prod)                                                |
| Logging              | [structlog](https://github.com/hynek/structlog) (JSON in production)       |
| Error tracking       | Sentry                                                                     |
| Testing              | pytest + pytest-asyncio                                                    |
| Linting / formatting | [Ruff](https://github.com/astral-sh/ruff)                                  |
| Package manager      | [uv](https://github.com/astral-sh/uv)                                      |
| API testing          | Postman                                                                    |
| Containerization     | Docker + Docker Compose                                                    |
| CI/CD                | GitHub Actions (lint, test, pip-audit)                                     |
| Deployment           | AWS EC2 (t4g.small, eu-north-1)                                            |

---

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- Redis 7+
- [uv](https://github.com/astral-sh/uv) (package manager)

### Local Setup

```bash
# Clone the repository
git clone https://github.com/oleksii-shcherbak/task-management-api.git
cd task-management-api

# Install dependencies
uv sync --all-groups

# Copy environment file and fill in the values
cp .env.example .env

# Create the database
createdb taskapi_db

# Run migrations
uv run alembic upgrade head

# Start the API server
uv run uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`, ReDoc at `http://localhost:8000/redoc`.

### Docker Setup

```bash
cp .env.example .env
# Fill in POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, JWT_SECRET_KEY

docker compose up --build
```

This starts the API, background worker, PostgreSQL, and Redis. Migrations run automatically on startup.

### Running Tests

```bash
# Create the test database first
createdb taskapi_test_db

# Run the full test suite
uv run pytest

# With coverage report
uv run pytest --cov=app --cov-report=term-missing
```

> Test coverage is reported at ~72%. The actual covered surface is higher — pytest-asyncio's session-scoped event loop
> causes under-attribution in async route handlers: coroutines execute and tests pass, but `coverage.py`'s `sys.settrace`
> loses attribution at the session boundary. This is a known framework limitation, not missing test coverage.

### Running the Background Worker

```bash
uv run arq app.worker.WorkerSettings
```

The worker handles email notifications and due-date reminders. Set `SMTP_HOST` in `.env` to enable sending; leave it
empty to log emails to stdout instead.
