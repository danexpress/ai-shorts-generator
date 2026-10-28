from conftest import assert_error, login
from social_helpers import callback, connect
from sqlalchemy import func, or_, select

from app.db_models import (
    Base,
    CaptionTrackRecord,
    ProjectRecord,
    RenderRecord,
    UploadRecord,
    UserRecord,
)
from app.social.adapters.base import ProviderError
from app.social.db_models import PublishBatch, PublishJob

ORIGIN = {"Origin": "http://127.0.0.1:3000"}
MAYA = "maya@example.com"


def user_id(database, email):
    with database.sessions() as db:
        return db.scalar(select(UserRecord.id).where(UserRecord.email == email))


def owned_keys(database, uid):
    """IDs of the user's projects, renders and sessions, found independently of the code."""
    from app.db_models import AuthSessionRecord

    with database.sessions() as db:
        return {
            "users": {uid},
            "projects": set(
                db.scalars(select(ProjectRecord.id).where(ProjectRecord.userId == uid))
            ),
            "renders": set(db.scalars(select(RenderRecord.id).where(RenderRecord.userId == uid))),
            "auth_sessions": set(
                db.scalars(select(AuthSessionRecord.id).where(AuthSessionRecord.userId == uid))
            ),
        }


def remaining_references(database, keys):
    """Rows in any table whose foreign keys still point at the user's records."""
    found = {}
    with database.sessions() as db:
        for table in Base.metadata.sorted_tables:
            conditions = [
                column.in_(keys[fk.column.table.name])
                for column in table.columns
                for fk in column.foreign_keys
                if keys.get(fk.column.table.name)
            ]
            if conditions:
                count = db.scalar(select(func.count()).select_from(table).where(or_(*conditions)))
                if count:
                    found[table.name] = count
        if db.get(UserRecord, next(iter(keys["users"]))):
            found["users"] = 1
    return found


def populate(client, headers, database, render, social):
    """A creator with media, captions, two platform connections and publishing history."""
    for platform in ("youtube", "x"):
        state, binding = connect(client, headers, platform)
        assert callback(client, state, binding, platform).status_code == 200
    uid = user_id(database, MAYA)
    with database.transaction() as db:
        record = db.get(RenderRecord, render)
        clip = record.clipId
        db.add(
            CaptionTrackRecord(
                projectId=record.projectId,
                clipId=clip,
                preset="clean-bold",
                keepFillers=False,
                edited=True,
                transcriptRevision=1,
                chunks=[],
                updatedAt=0,
            )
        )
        db.add(
            PublishBatch(
                id="pbt_1",
                user_id=uid,
                render_id=render,
                project_id=record.projectId,
                idempotency_key="k",
                payload_hash="h",
                created_at=0,
            )
        )
        db.flush()
        db.add(
            PublishJob(
                id="pjb_1",
                publish_batch_id="pbt_1",
                user_id=uid,
                render_id=render,
                platform="youtube",
                platform_user_id="creator-1",
                status="queued",
                post_metadata={"title": "private draft"},
                next_attempt_at=0,
                created_at=0,
                updated_at=0,
            )
        )
    return uid


def test_deleting_an_account_removes_everything_it_owns(
    client, headers, database, settings, render, social
):
    uid = populate(client, headers, database, render, social)
    keys = owned_keys(database, uid)
    other = user_id(database, "jon@example.com")
    other_projects = owned_keys(database, other)["projects"]
    media = list((settings.media_dir).rglob("*.mp4"))
    assert media and keys["projects"] and keys["renders"]

    response = client.request(
        "DELETE", "/v1/me", headers=headers, json={"confirmEmail": " MAYA@example.com "}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "revokedPlatforms": ["youtube", "x"],
        "notRevokedPlatforms": [],
    }
    assert remaining_references(database, keys) == {}
    with database.sessions() as db:
        assert not db.scalar(
            select(func.count()).select_from(UploadRecord).where(UploadRecord.userId == uid)
        )
    # Media files are deleted; other creators keep their data and access.
    assert not any(
        path.exists()
        for path in media
        if path.stem.startswith(tuple(keys["renders"] | keys["projects"]))
    )
    assert owned_keys(database, other)["projects"] == other_projects
    assert client.get("/v1/me", headers=login(client, "jon@example.com")).status_code == 200
    # The deleted account can no longer authenticate by any means.
    assert_error(client.get("/v1/me", headers=headers), 401, "UNAUTHENTICATED")
    assert_error(
        client.post("/v1/auth/google", json={"email": MAYA, "password": "DemoPass123!"}),
        401,
        "UNAUTHENTICATED",
    )


def test_deletion_requires_the_account_email(client, headers, database):
    response = client.request(
        "DELETE", "/v1/me", headers=headers, json={"confirmEmail": "jon@example.com"}
    )
    assert_error(response, 422, "CONFIRMATION_MISMATCH")
    assert_error(client.request("DELETE", "/v1/me", headers=headers, json={}), 422, "INVALID_INPUT")
    assert user_id(database, MAYA)


def test_cookie_sessions_need_csrf_and_lose_their_cookies(client, database):
    response = client.post("/v1/auth/google", json={"email": MAYA, "password": "DemoPass123!"})
    csrf = response.json()["csrfToken"]
    body = {"confirmEmail": MAYA}
    assert_error(client.request("DELETE", "/v1/me", headers=ORIGIN, json=body), 403, "CSRF_FAILED")
    deleted = client.request(
        "DELETE", "/v1/me", headers={**ORIGIN, "X-CSRF-Token": csrf}, json=body
    )
    assert deleted.status_code == 200, deleted.text
    cookies = deleted.headers.get_list("set-cookie")
    assert any(c.startswith("shorts_session=") and "Max-Age=0" in c for c in cookies)
    assert user_id(database, MAYA) is None
    assert_error(client.get("/v1/me"), 401, "UNAUTHENTICATED")


def test_failed_provider_revocation_is_reported_but_tokens_are_still_deleted(
    client, headers, database, social
):
    from app.social.db_models import SocialAccount

    _service, adapter = social
    state, binding = connect(client, headers, "tiktok")
    callback(client, state, binding, "tiktok")

    def down(token):
        raise ProviderError("PROVIDER_UNAVAILABLE", retryable=True)

    adapter.disconnect = down
    response = client.request("DELETE", "/v1/me", headers=headers, json={"confirmEmail": MAYA})
    assert response.json()["notRevokedPlatforms"] == ["tiktok"]
    with database.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SocialAccount)) == 0


def test_running_jobs_are_cancelled_and_the_worker_lets_go(
    client, headers, database, settings, clock
):
    import dataclasses

    from conftest import uploaded

    from app.jobs.worker import ProcessingWorker

    project = uploaded(client, headers)
    client.post(f"/v1/projects/{project}/process", headers=headers, json={"goal": "educational"})
    response = client.request("DELETE", "/v1/me", headers=headers, json={"confirmEmail": MAYA})
    assert response.status_code == 200, response.text
    local = dataclasses.replace(settings, processing_mode="local")
    assert ProcessingWorker(database, local, transcriber=object(), clock=clock).run_once() is False
