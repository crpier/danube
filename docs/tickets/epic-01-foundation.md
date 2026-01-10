# Epic 1: Foundation

**Description**: Set up project structure, logging, configuration management, database layer, and encryption.

**Total Estimate**: 24 hours

---

## DANUBE-001: Project Setup

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 2h  
**Dependencies:** None  
**Assignee:** Unassigned

### Description

Initialize Python project with UV, set up directory structure, configure pyright for strict type checking, and add pre-commit hooks.

### Acceptance Criteria

- [ ] `pyproject.toml` configured with UV
- [ ] Project structure created (danube/, tests/, docs/)
- [ ] Pyright configured in strict mode
- [ ] Pre-commit hooks installed (ruff, pyright)
- [ ] README.md with project overview
- [ ] `.gitignore` configured for Python
- [ ] License file added (MIT or Apache 2.0)

### Technical Notes

Use pyproject.toml for all configuration:
```toml
[project]
name = "danube"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "sqlalchemy[asyncio]",
    ...
]

[tool.pyright]
typeCheckingMode = "strict"
pythonVersion = "3.11"
```

---

## DANUBE-002: Logging & Configuration

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 4h  
**Dependencies:** DANUBE-001  
**Assignee:** Unassigned

### Description

Implement structured logging with JSON output and configuration loading from TOML file with environment variable overrides.

### Acceptance Criteria

- [ ] Structured logging configured (JSON format)
- [ ] Log levels configurable (trace, debug, info, warn, error)
- [ ] Configuration loaded from `/etc/danube/danube.toml`
- [ ] Environment variable overrides implemented
- [ ] Configuration validation on startup
- [ ] Configuration dataclasses with Pydantic
- [ ] Tests for configuration loading
- [ ] Tests for logging output

### Technical Notes

Use Python's `logging` with JSON formatter:
```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **record.__dict__.get("extra", {})
        }
        return json.dumps(log_data)
```

---

## DANUBE-003: SQLite + SQLAlchemy Async Setup

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 6h  
**Dependencies:** DANUBE-002  
**Assignee:** Unassigned

### Description

Set up SQLAlchemy 2.0 with async engine, configure SQLite with WAL mode, and integrate Alembic for migrations.

### Acceptance Criteria

- [ ] SQLAlchemy async engine configured
- [ ] SQLite WAL mode enabled
- [ ] Connection pooling configured
- [ ] Alembic migration framework integrated
- [ ] Initial migration creates empty database
- [ ] Database session factory created
- [ ] Tests with in-memory SQLite
- [ ] Error handling for database failures

### Technical Notes

SQLite configuration:
```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine(
    f"sqlite+aiosqlite:///{db_path}",
    connect_args={"check_same_thread": False},
    echo=False
)

# Enable WAL mode
async with engine.begin() as conn:
    await conn.execute(text("PRAGMA journal_mode=WAL"))
    await conn.execute(text("PRAGMA synchronous=NORMAL"))
    await conn.execute(text("PRAGMA foreign_keys=ON"))

SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

---

## DANUBE-004: Database Schema & Migrations

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-003  
**Assignee:** Unassigned

### Description

Define SQLAlchemy models for all database tables (users, teams, pipelines, jobs, steps, secrets, artifacts) and create initial migration.

### Acceptance Criteria

- [ ] All models defined with type hints
- [ ] Relationships configured (ForeignKey, cascade)
- [ ] Indexes created for common queries
- [ ] Default values set (timestamps, status)
- [ ] Alembic migration generated
- [ ] Migration tested (upgrade/downgrade)
- [ ] Repository pattern implemented
- [ ] Tests for all models
- [ ] Tests for repositories

### Technical Notes

Use SQLAlchemy 2.0 style with mapped_column:
```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class Job(Base):
    __tablename__ = "jobs"
    
    id: Mapped[str] = mapped_column(primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id"))
    status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

---

## DANUBE-005: Encryption Service

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 4h  
**Dependencies:** DANUBE-004  
**Assignee:** Unassigned

### Description

Implement AES-256-GCM encryption service for secrets with key management and in-memory cache.

### Acceptance Criteria

- [ ] Encryption key generation (32 bytes)
- [ ] AES-256-GCM encryption implemented
- [ ] Decryption with authentication
- [ ] Key loaded from `/var/lib/danube/keys/encryption.key`
- [ ] In-memory secret cache (per job)
- [ ] Cache invalidation when job completes
- [ ] Tests for encryption/decryption
- [ ] Tests for cache operations
- [ ] Error handling for corrupted ciphertext

### Technical Notes

Use `cryptography` library:
```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

class EncryptionService:
    def __init__(self, key_path: str):
        with open(key_path, "rb") as f:
            self.key = f.read()
        self.aesgcm = AESGCM(self.key)
    
    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ciphertext
    
    def decrypt(self, encrypted: bytes) -> bytes:
        nonce = encrypted[:12]
        ciphertext = encrypted[12:]
        return self.aesgcm.decrypt(nonce, ciphertext, None)
```

---

## Updates

- 2026-01-10: Epic created
