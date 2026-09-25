from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import SOURCE, assert_error, login, ready, record, rows, uploaded
from fastapi.testclient import TestClient
from sqlalchemy import create_mock_engine
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db_models import (
    ActiveJobRecord,
    Base,
    JobRecord,
    LedgerRecord,
    ProjectRecord,
    TokenRecord,
    TranscriptRecord,
    UploadRecord,
    UserRecord,
)
from app.main import create_app
from app.store import Store


def test_database_url_comes_from_environment(monkeypatch, tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'configured.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    app = create_app()
    assert app.state.settings.database_url == url
    # Engine creation/import does not create schema, seed, or open a database file.
    assert not (tmp_path / "configured.db").exists()
    with TestClient(app) as client:
        assert client.get("/v1/me", headers=login(client)).status_code == 200
        assert str(app.state.database.engine.url) == url
    assert (tmp_path / "configured.db").is_file()


def test_default_database_path_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    before = Settings().database_url
    monkeypatch.chdir(tmp_path)
    assert Settings().database_url == before
    assert before.endswith("/backend/ai_shorts.db")


def test_restart_preserves_users_tokens_edits_and_pending_uploads(settings, clock):
    first = create_app(settings=settings, clock=clock)
    with TestClient(first) as client:
        headers = login(client)
        me = client.get("/v1/me", headers=headers).json()
        seeded = client.get("/v1/projects", headers=headers).json()
        pid = next(p["id"] for p in seeded if p["status"] == "ready")
        edited = client.patch(
            f"/v1/projects/{pid}/transcript",
            headers=headers,
            json={"segments": [{"id": "seg_0", "text": "A persistent correction — café."}]},
        ).json()
        pending = client.post("/v1/uploads", headers=headers, json=SOURCE).json()
        user_before = record(first.state.database, UserRecord, me["user"]["id"])
        counts = {
            m: len(rows(first.state.database, m)) for m in (UserRecord, ProjectRecord, LedgerRecord)
        }
        with first.state.database.transaction() as session:
            session.get(UserRecord, me["user"]["id"]).limitMinutes = 75
    # New engine and new app: no shared session or Python data store.
    second = create_app(settings=settings, clock=clock)
    with TestClient(second) as client:
        assert client.get("/v1/me", headers=headers).json()["user"] == me["user"]
        assert client.get("/v1/me", headers=headers).json()["usage"]["limitMinutes"] == 75
        assert client.get(f"/v1/projects/{pid}/transcript", headers=headers).json() == edited
        assert client.get(f"/v1/projects/{pid}/clips", headers=headers).json()["stale"]
        assert (
            client.post(f"/v1/uploads/{pending['uploadId']}/complete", headers=headers).status_code
            == 200
        )
        for model, count in counts.items():
            assert len(rows(second.state.database, model)) == count
        user_after = record(second.state.database, UserRecord, me["user"]["id"])
        assert user_after["passwordHash"] == user_before["passwordHash"]
        assert second.state.database is not first.state.database


def test_job_progress_and_idempotency_survive_multiple_restarts(settings, clock):
    key = "restart-process"
    first = create_app(settings=settings, clock=clock)
    with TestClient(first) as client:
        headers = login(client)
        pid = uploaded(client, headers)
        idem = {**headers, "Idempotency-Key": key}
        started = client.post(
            f"/v1/projects/{pid}/process", headers=idem, json={"goal": "viral"}
        ).json()
    clock.advance(5)
    second = create_app(settings=settings, clock=clock)
    with TestClient(second) as client:
        status = client.get(f"/v1/projects/{pid}/status", headers=headers).json()
        assert status["stage"] == "analyzing" and status["jobId"] == started["jobId"]
        assert (
            client.post(f"/v1/projects/{pid}/process", headers=idem, json={"goal": "viral"}).json()
            == started
        )
        assert (
            len([r for r in rows(second.state.database, LedgerRecord) if r["projectId"] == pid])
            == 1
        )
    clock.advance(3)
    third = create_app(settings=settings, clock=clock)
    with TestClient(third) as client:
        assert client.get(f"/v1/projects/{pid}/status", headers=headers).json()["status"] == "ready"
        assert client.get(f"/v1/projects/{pid}/clips", headers=headers).json()["clips"]
        assert (
            client.post(f"/v1/projects/{pid}/process", headers=idem, json={"goal": "viral"}).json()
            == started
        )
        assert len([r for r in rows(third.state.database, JobRecord) if r["projectId"] == pid]) == 1
        assert (
            len([r for r in rows(third.state.database, LedgerRecord) if r["projectId"] == pid]) == 1
        )
        assert not rows(third.state.database, ActiveJobRecord)


def test_logout_revocation_survives_restart(settings, clock):
    with TestClient(create_app(settings=settings, clock=clock)) as client:
        headers = login(client)
        assert client.post("/v1/auth/logout", headers=headers).status_code == 200
    with TestClient(create_app(settings=settings, clock=clock)) as client:
        assert_error(client.get("/v1/me", headers=headers), 401, "UNAUTHENTICATED")


def test_delete_is_not_undone_by_seeding_on_restart(settings, clock):
    first = create_app(settings=settings, clock=clock)
    with TestClient(first) as client:
        headers = login(client)
        pid = next(
            p["id"]
            for p in client.get("/v1/projects", headers=headers).json()
            if p["status"] == "ready"
        )
        assert client.delete(f"/v1/projects/{pid}", headers=headers).status_code == 200
    second = create_app(settings=settings, clock=clock)
    with TestClient(second) as client:
        assert_error(client.get(f"/v1/projects/{pid}", headers=headers), 404, "NOT_FOUND")
        assert record(second.state.database, TranscriptRecord, pid) is None
        assert len([p for p in rows(second.state.database, ProjectRecord) if p["id"] == pid]) == 1


def test_failed_request_rolls_back_all_writes(client, database, headers, monkeypatch):
    before_projects = rows(database, ProjectRecord)
    before_uploads = rows(database, UploadRecord)
    original = Store.add_upload

    def broken_upload(self, fields):
        original(self, fields)
        self.db.flush()  # Both the project and upload have actually reached SQLite.
        raise RuntimeError("simulated failure after database writes")

    monkeypatch.setattr(Store, "add_upload", broken_upload)
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.post("/v1/uploads", headers=headers, json=SOURCE)
    assert rows(database, ProjectRecord) == before_projects
    assert rows(database, UploadRecord) == before_uploads
    assert client.get("/v1/me", headers=headers).status_code == 200


def test_separate_apps_share_active_job_limit_and_idempotency(settings, clock):
    first = create_app(settings=settings, clock=clock)
    second = create_app(settings=settings, clock=clock)
    with TestClient(first) as a, TestClient(second) as b:
        headers = login(a)
        pid = uploaded(a, headers)
        other = uploaded(a, headers)
        assert b.get(f"/v1/projects/{pid}", headers=headers).status_code == 200

        def start(client):
            return client.post(
                f"/v1/projects/{pid}/process",
                headers={**headers, "Idempotency-Key": "concurrent"},
                json={"goal": "viral"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, [a, b]))
        assert all(r.status_code == 200 for r in results)
        assert results[0].json() == results[1].json()
        assert_error(
            b.post(f"/v1/projects/{other}/process", headers=headers, json={"goal": "viral"}),
            409,
            "ACTIVE_JOB_EXISTS",
        )
        clock.advance(8)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda c: c.get(f"/v1/projects/{pid}/status", headers=headers), [a, b])
            )
        assert all(r.json()["status"] == "ready" for r in results)
        assert (
            len([e for e in rows(first.state.database, LedgerRecord) if e["projectId"] == pid]) == 1
        )


def test_competing_projects_across_apps_cannot_both_start(settings, clock):
    with (
        TestClient(create_app(settings=settings, clock=clock)) as a,
        TestClient(create_app(settings=settings, clock=clock)) as b,
    ):
        headers = login(a)
        pids = [uploaded(a, headers), uploaded(b, headers)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda pair: pair[0].post(
                        f"/v1/projects/{pair[1]}/process", headers=headers, json={"goal": "viral"}
                    ),
                    zip([a, b], pids),
                )
            )
        assert sorted(r.status_code for r in responses) == [200, 409]


def test_sqlite_enforces_foreign_keys_and_unique_usage(client, database, headers, clock):
    pid = ready(client, headers, clock)
    ledger = next(e for e in rows(database, LedgerRecord) if e["projectId"] == pid)
    with pytest.raises(IntegrityError), database.transaction() as session:
        session.add(LedgerRecord(**{**ledger, "id": "duplicate-charge"}))
    assert len([e for e in rows(database, LedgerRecord) if e["projectId"] == pid]) == 1
    with pytest.raises(IntegrityError), database.transaction() as session:
        session.add(TokenRecord(digest="a" * 64, userId="missing-user", expiresAt=123))
    assert record(database, TokenRecord, "a" * 64) is None


def test_postgresql_schema_compiles_without_sqlite_specific_types():
    statements = []
    engine = create_mock_engine(
        "postgresql://",
        lambda sql, *args, **kw: statements.append(str(sql.compile(dialect=engine.dialect))),
    )
    Base.metadata.create_all(engine)
    ddl = "\n".join(statements)
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table}" in ddl
    assert "UNIQUE (project_id, usage_type)" in ddl
    assert "FOREIGN KEY" in ddl and "JSON" in ddl
    assert "PRAGMA" not in ddl and "AUTOINCREMENT" not in ddl


def test_optional_in_memory_sqlite_for_isolated_tests(clock):
    app = create_app(settings=Settings(database_url="sqlite+pysqlite:///:memory:"), clock=clock)
    with TestClient(app) as client:
        assert client.get("/v1/me", headers=login(client)).status_code == 200


def test_seeding_can_be_disabled_with_environment(monkeypatch, tmp_path, clock):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'unseeded.db'}")
    monkeypatch.setenv("SEED_DEMO_DATA", "false")
    app = create_app(clock=clock)
    with TestClient(app) as client:
        assert rows(app.state.database, UserRecord) == []
        assert_error(
            client.post(
                "/v1/auth/google", json={"email": "maya@example.com", "password": "DemoPass123!"}
            ),
            401,
            "UNAUTHENTICATED",
        )
