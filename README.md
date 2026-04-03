# Task Management API

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

| Layer                | Technology                              |
|----------------------|-----------------------------------------|
| Framework            | FastAPI 0.128+                          |
| Language             | Python 3.12                             |
| Validation           | Pydantic v2                             |
| Database             | PostgreSQL 18 (async via asyncpg)       |
| ORM / migrations     | SQLAlchemy 2.0 (Mapped style) + Alembic |
| Cache / queue broker | Redis 8                                 |
| Background tasks     | ARQ                                     |
| Authentication       | PyJWT + bcrypt                          |
| File storage         | Local (dev) / AWS S3 (prod)             |
| Logging              | structlog (JSON in production)          |
| Error tracking       | Sentry                                  |
| Testing              | pytest + pytest-asyncio                 |
| Linting / formatting | Ruff                                    |
| Package manager      | uv                                      |
| API testing          | Postman                                 |
| Containerization     | Docker + Docker Compose                 |
| CI/CD                | GitHub Actions (lint, test, pip-audit)  |
| Deployment           | AWS EC2 (t4g.small, eu-north-1)         |
