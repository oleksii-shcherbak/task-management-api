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

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`, ReDoc at
`http://localhost:8000/redoc`.

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
> causes under-attribution in async route handlers: coroutines execute and tests pass, but `coverage.py`'s
`sys.settrace`
> loses attribution at the session boundary. This is a known framework limitation, not missing test coverage.

### Running the Background Worker

```bash
uv run arq app.worker.WorkerSettings
```

The worker handles email notifications and due-date reminders. Set `SMTP_HOST` in `.env` to enable sending; leave it
empty to log emails to stdout instead.

---

## API Reference

All endpoints are prefixed with `/api/v1`. Protected endpoints require `Authorization: Bearer <access_token>`.

The Postman collection in `postman/` covers all endpoints with pre-written test scripts and automatic token/variable
saving.

### Auth

| Method | Path                   | Description                    |
|--------|------------------------|--------------------------------|
| `POST` | `/register`            | Register and receive tokens    |
| `POST` | `/login`               | Log in with email or username  |
| `POST` | `/refresh`             | Rotate refresh token           |
| `POST` | `/logout`              | Revoke refresh token           |
| `GET`  | `/verify-email`        | Verify email via query token   |
| `POST` | `/resend-verification` | Resend verification email      |
| `GET`  | `/github`              | Start GitHub OAuth flow        |
| `POST` | `/forgot-password`     | Request password reset email   |
| `POST` | `/reset-password`      | Reset password via email token |

### Users

| Method   | Path           | Description                             |
|----------|----------------|-----------------------------------------|
| `GET`    | `/me`          | Get current user profile                |
| `PATCH`  | `/me`          | Update name or username                 |
| `PATCH`  | `/me/password` | Change password                         |
| `DELETE` | `/me`          | Soft-delete account                     |
| `POST`   | `/me/avatar`   | Upload avatar (JPEG/PNG/GIF/WebP, 2 MB) |
| `DELETE` | `/me/avatar`   | Remove avatar                           |
| `GET`    | `/me/mentions` | Paginated @mention inbox                |
| `GET`    | `/{id}`        | Public profile (name + avatar only)     |

### Projects

| Method   | Path                           | Description                      |
|----------|--------------------------------|----------------------------------|
| `POST`   | `/`                            | Create project                   |
| `GET`    | `/`                            | List projects (cursor-paginated) |
| `GET`    | `/{id}`                        | Get project                      |
| `PATCH`  | `/{id}`                        | Update project                   |
| `DELETE` | `/{id}`                        | Delete project                   |
| `GET`    | `/{id}/statuses`               | List task statuses               |
| `POST`   | `/{id}/members`                | Add member                       |
| `GET`    | `/{id}/members`                | List members                     |
| `GET`    | `/{id}/members/search`         | Search members by name           |
| `PATCH`  | `/{id}/members/{user_id}/role` | Update member role               |
| `DELETE` | `/{id}/members/{user_id}`      | Remove member                    |

### Tasks

| Method   | Path                   | Description                                                             |
|----------|------------------------|-------------------------------------------------------------------------|
| `POST`   | `/projects/{id}/tasks` | Create task                                                             |
| `GET`    | `/projects/{id}/tasks` | List tasks (paginated, filterable by status/priority/assignee)          |
| `GET`    | `/tasks/{id}`          | Get task                                                                |
| `PATCH`  | `/tasks/{id}`          | Update task (title, description, status, assignees, priority, due date) |
| `PATCH`  | `/tasks/{id}/position` | Reorder task within status column                                       |
| `DELETE` | `/tasks/{id}`          | Delete task                                                             |
| `GET`    | `/tasks/{id}/activity` | Task activity log                                                       |

### Comments

| Method   | Path                                      | Description                      |
|----------|-------------------------------------------|----------------------------------|
| `POST`   | `/projects/{id}/tasks/{task_id}/comments` | Add comment (supports @mentions) |
| `GET`    | `/projects/{id}/tasks/{task_id}/comments` | List comments (cursor-paginated) |
| `PATCH`  | `/comments/{id}`                          | Edit comment                     |
| `DELETE` | `/comments/{id}`                          | Delete comment                   |

### Attachments

| Method   | Path                      | Description         |
|----------|---------------------------|---------------------|
| `POST`   | `/tasks/{id}/attachments` | Upload file (10 MB) |
| `GET`    | `/tasks/{id}/attachments` | List attachments    |
| `GET`    | `/attachments/{id}/url`   | Get download URL    |
| `DELETE` | `/attachments/{id}`       | Delete attachment   |

### Statuses

| Method   | Path           | Description                                   |
|----------|----------------|-----------------------------------------------|
| `POST`   | `/`            | Create custom status                          |
| `PATCH`  | `/{status_id}` | Update name, color, default flag, or position |
| `DELETE` | `/{status_id}` | Delete status (with optional task migration)  |

---

## Deployment

The production instance runs on a single AWS EC2 t4g.small (Graviton2, eu-north-1). All services — API, background
worker, PostgreSQL, and Redis — run in Docker Compose on the same instance. Database migrations run automatically on
container startup via the entrypoint script.

```bash
# Production deploy (run on the server)
docker compose -f docker-compose.yml up -d --build
```

The GitHub Actions CI pipeline runs on every push: linting with Ruff, integration tests with pytest, and dependency
vulnerability scanning with pip-audit.

---

## License

MIT — see [LICENSE](LICENSE).
