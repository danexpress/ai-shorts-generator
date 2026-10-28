"""Permanent account deletion.

Everything owned by the user is deleted in one transaction: projects and their media
files (through Store.delete, which also cancels processing and queued posts),
sessions and tokens, social connections with their encrypted platform tokens,
publishing history, usage and events. Rows are found by following foreign keys
outward from the user, so tables added later are covered automatically.

After the commit, each platform is asked to revoke the access token (best effort,
outside the transaction). Posts already published on platforms are not deleted.
"""

import logging

from sqlalchemy import delete, or_, select

from .db_models import Base, ProjectRecord, UserRecord
from .errors import ApiError
from .social.adapters.base import ProviderError
from .social.db_models import OAuthState, SocialAccount

logger = logging.getLogger(__name__)


def owned_ids(db, user_id: str) -> dict[str, set]:
    """Primary keys of every row that belongs to the user, per table (FK closure)."""
    owned = {"users": {user_id}}
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.sorted_tables:
            if table.name == "users":
                continue
            conditions = [
                column.in_(owned[fk.column.table.name])
                for column in table.columns
                for fk in column.foreign_keys
                if fk.column.table.name in owned and fk.column.primary_key
            ]
            if not conditions or len(table.primary_key.columns) != 1:
                continue
            key = next(iter(table.primary_key.columns))
            found = set(db.scalars(select(key).where(or_(*conditions))))
            if not found <= owned.get(table.name, set()):
                owned[table.name] = owned.get(table.name, set()) | found
                changed = True
    return owned


def purge(db, user_id: str):
    """Delete every row referencing the user, directly or through owned rows."""
    owned = owned_ids(db, user_id)
    for table in reversed(Base.metadata.sorted_tables):  # children before parents
        if table.name == "users":
            continue
        conditions = [
            column.in_(owned[fk.column.table.name])
            for column in table.columns
            for fk in column.foreign_keys
            if fk.column.table.name in owned
        ]
        if conditions:
            db.execute(delete(table).where(or_(*conditions)))
    db.execute(delete(UserRecord).where(UserRecord.id == user_id))


def delete_account(app, user_id: str) -> dict:
    """Delete the account; returns which platforms revoked their tokens."""
    from .store import Store

    accounts = app.state.social_accounts
    revocations = []
    with app.state.database.transaction() as db:
        store = Store(db, app.state.settings, app.state.clock)
        user = db.get(UserRecord, user_id)
        if user is None:
            return {"revoked": [], "notRevoked": []}
        user.active = False  # stop concurrent requests and workers immediately
        for project in db.scalars(
            select(ProjectRecord).where(
                ProjectRecord.userId == user_id, ProjectRecord.status != "deleted"
            )
        ).all():
            store.delete(project)  # cancels jobs/posts and removes media files
        for account in db.scalars(select(SocialAccount).where(SocialAccount.user_id == user_id)):
            try:
                token = accounts.cipher.decrypt(
                    account.access_token_encrypted, user_id, account.platform
                )
            except ApiError:  # undecryptable: nothing to revoke, still deleted
                token = None
            revocations.append((account.platform, token))
        db.execute(delete(OAuthState).where(OAuthState.user_id == user_id))
        purge(db, user_id)
    revoked, not_revoked = [], []
    for platform, token in revocations:
        adapter = accounts.adapters.get(platform)
        try:
            if token is None or adapter is None:
                raise ProviderError("NOT_REVOKED")
            adapter.disconnect(token)
            revoked.append(platform)
        except (ProviderError, ApiError, OSError) as exc:  # local tokens are already gone
            logger.warning(
                "Token revocation failed for %s during account deletion: %s",
                platform,
                exc.code if isinstance(exc, ProviderError) else type(exc).__name__,
            )
            not_revoked.append(platform)
    logger.info("Deleted account %s (%d platform connection(s))", user_id, len(revocations))
    return {"revoked": revoked, "notRevoked": not_revoked}
