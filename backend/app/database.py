"""Engine/session lifecycle. SQLite adaptations are confined to this module."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import create_engine, event, inspect, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .db_models import Base, DatabaseState


class Database:
    def __init__(self, url: str):
        options = {"pool_pre_ping": True}
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        self.memory = parsed.get_backend_name() == "sqlite" and parsed.database in (
            None,
            "",
            ":memory:",
        )
        if parsed.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if self.memory:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        # A shared in-memory SQLite connection cannot host concurrent transactions.
        # File databases serialize via the database row lock below, across processes.
        self._memory_lock = Lock()
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, record):
                connection.isolation_level = None
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            @event.listens_for(self.engine, "begin")
            def begin_sqlite(connection):
                connection.exec_driver_sql("BEGIN")

    def initialize(self):
        Base.metadata.create_all(self.engine)
        # Additive migration for databases created before unlimited creator usage
        # was supported. This ALTER is supported by SQLite and PostgreSQL.
        if "unlimited_usage" not in {
            column["name"] for column in inspect(self.engine).get_columns("users")
        }:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN unlimited_usage BOOLEAN NOT NULL DEFAULT FALSE"
                )
        upload_columns = {column["name"] for column in inspect(self.engine).get_columns("uploads")}
        with self.engine.begin() as connection:
            if "stored_name" not in upload_columns:
                connection.exec_driver_sql("ALTER TABLE uploads ADD COLUMN stored_name VARCHAR(80)")
            if "content_type" not in upload_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE uploads ADD COLUMN content_type VARCHAR(128)"
                )
        try:
            with self.sessions.begin() as session:
                session.add(DatabaseState(id=1, revision=0, demoSeeded=False))
        except IntegrityError:
            # Another startup (or an earlier run) already created the lock/seed marker.
            with self.sessions() as session:
                if session.get(DatabaseState, 1) is None:
                    raise

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        from contextlib import nullcontext

        with (
            self._memory_lock if self.memory else nullcontext(),
            self.sessions.begin() as session,
        ):
            # Every API call may advance demo jobs. Serialize those transitions
            # with a portable transactional UPDATE, not a process-local lock.
            # PostgreSQL acquires a row lock; SQLite acquires its write lock.
            result = session.execute(
                update(DatabaseState)
                .where(DatabaseState.id == 1)
                .values(revision=DatabaseState.revision + 1)
            )
            if result.rowcount != 1:
                raise RuntimeError("Database has not been initialized")
            yield session

    def dispose(self):
        self.engine.dispose()
