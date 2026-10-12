"""Getrennte Haushalte mit persönlichen Anmeldungen und Kontoeinladungen."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time

from fastapi import HTTPException

from .db import Database

INVITATION_TTL = 7 * 24 * 60 * 60
MAX_ACCOUNT_MEMBERS = 2


def migrate_preferences(connection):
    if 'default_servings' not in {row[1] for row in connection.execute('PRAGMA table_info(user_accounts)')}:
        connection.execute("ALTER TABLE user_accounts ADD COLUMN default_servings INTEGER NOT NULL DEFAULT 2 "
                           "CHECK(typeof(default_servings)='integer' AND default_servings BETWEEN 1 AND 24)")
    connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(274,'household_default_servings',?)", (time.time(),))


def preferences_for_user(db, user_id):
    """Read without creating a household during login or session checks."""
    with db.conn() as connection:
        row = connection.execute('SELECT a.id,a.default_servings FROM user_accounts a JOIN account_members m '
                                 'ON m.account_id=a.id WHERE m.user_id=?', (user_id,)).fetchone()
    return {'household_id': row['id'] if row else None, 'default_servings': row['default_servings'] if row else 2}


def default_servings(db):
    account_id = getattr(db, 'account_id', 0)
    with db.conn() as connection:
        row = connection.execute('SELECT default_servings FROM user_accounts WHERE id=?', (account_id,)).fetchone()
    return int(row[0]) if row else 2


def save_preferences(db, user_id, servings):
    if type(servings) is not int or not 1 <= servings <= 24:
        raise HTTPException(422, 'Portionszahl muss zwischen 1 und 24 liegen')
    with db.conn() as connection:
        connection.execute('BEGIN IMMEDIATE')
        account = _account(connection, user_id)
        if hasattr(db, 'account_id') and db.account_id != account['id']:
            raise HTTPException(409, 'Dein Haushalt hat sich geändert. Bitte erneut laden.')
        connection.execute('UPDATE user_accounts SET default_servings=? WHERE id=?', (servings, account['id']))
    return {'ok': True, 'household_id': account['id'], 'default_servings': servings}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _account(connection, user_id: int):
    row = connection.execute(
        "SELECT a.* FROM user_accounts a JOIN account_members m ON m.account_id=a.id WHERE m.user_id=?",
        (user_id,),
    ).fetchone()
    if row:
        return row
    now = time.time()
    account_id = connection.execute(
        "INSERT INTO user_accounts(owner_user_id, created_at) VALUES (?, ?)", (user_id, now),
    ).lastrowid
    connection.execute("INSERT INTO account_members VALUES (?, ?, ?)", (account_id, user_id, now))
    return connection.execute("SELECT * FROM user_accounts WHERE id=?", (account_id,)).fetchone()


def _invitation(connection, token: str):
    row = connection.execute(
        "SELECT i.*, a.owner_user_id, u.disabled FROM account_invitations i "
        "JOIN user_accounts a ON a.id=i.account_id JOIN users u ON u.id=a.owner_user_id "
        "WHERE i.token_hash=?", (_token_hash(token),),
    ).fetchone()
    if not row or row["disabled"] or row["revoked_at"] is not None or row["accepted_at"] is not None or row["expires_at"] <= time.time():
        raise HTTPException(400, "Einladung ist ungültig, abgelaufen oder bereits verwendet")
    count = connection.execute("SELECT COUNT(*) FROM account_members WHERE account_id=?", (row["account_id"],)).fetchone()[0]
    if count >= MAX_ACCOUNT_MEMBERS:
        raise HTTPException(409, "Das Konto hat bereits zwei Personen")
    return row


def _join(connection, invitation, user_id: int):
    now = time.time()
    connection.execute("INSERT INTO account_members VALUES (?, ?, ?)", (invitation["account_id"], user_id, now))
    connection.execute("UPDATE account_invitations SET accepted_at=?, accepted_by=? WHERE id=?", (now, user_id, invitation["id"]))
    connection.execute(
        "UPDATE account_invitations SET revoked_at=? WHERE account_id=? AND accepted_at IS NULL AND revoked_at IS NULL",
        (now, invitation["account_id"]),
    )


def register(db: Database, username: str, password_hash: str, *, invitation_token: str = "") -> int:
    try:
        with db.conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Ohne eingerichteten Betreiber darf eine öffentliche Registrierung
            # nicht beim nächsten Start zum ersten Administrator werden.
            if not connection.execute("SELECT 1 FROM users WHERE role='admin' AND disabled=0 LIMIT 1").fetchone():
                raise HTTPException(503, "Der Rezeptserver muss zuerst vom Betreiber eingerichtet werden")
            invitation = _invitation(connection, invitation_token) if invitation_token else None
            user_id = int(connection.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
                (username, password_hash, time.time()),
            ).lastrowid)
            if invitation:
                _join(connection, invitation, user_id)
            else:
                _account(connection, user_id)
            return user_id
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Dieser Benutzername ist bereits vergeben") from exc


def view(db: Database, user_id: int) -> dict:
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, user_id)
        members = [dict(row) for row in connection.execute(
            "SELECT u.id, u.username, u.disabled, m.joined_at FROM account_members m "
            "JOIN users u ON u.id=m.user_id WHERE m.account_id=? ORDER BY m.joined_at, u.id", (account["id"],),
        )]
        invitations = [dict(row) for row in connection.execute(
            "SELECT id, created_at, expires_at, revoked_at, accepted_at FROM account_invitations "
            "WHERE account_id=? ORDER BY created_at DESC LIMIT 20", (account["id"],),
        )] if account["owner_user_id"] == user_id else []
        return {"id": account["id"], "is_owner": account["owner_user_id"] == user_id,
                "members": members, "invitations": invitations, "max_members": MAX_ACCOUNT_MEMBERS,
                "data_scope": "household", "global_recipes": True, "default_servings": account['default_servings']}


def invite(db: Database, user_id: int) -> dict:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = _account(connection, user_id)
        if account["owner_user_id"] != user_id:
            raise HTTPException(403, "Nur die kontoverantwortliche Person kann einladen")
        count = connection.execute("SELECT COUNT(*) FROM account_members WHERE account_id=?", (account["id"],)).fetchone()[0]
        if count >= MAX_ACCOUNT_MEMBERS:
            raise HTTPException(409, "Das Konto hat bereits zwei Personen")
        connection.execute(
            "UPDATE account_invitations SET revoked_at=? WHERE account_id=? AND revoked_at IS NULL AND accepted_at IS NULL",
            (now, account["id"]),
        )
        invitation_id = connection.execute(
            "INSERT INTO account_invitations(account_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (account["id"], _token_hash(token), now, now + INVITATION_TTL),
        ).lastrowid
    return {"id": invitation_id, "token": token, "invite_path": "/register?invite=" + token,
            "expires_at": now + INVITATION_TTL}


def revoke(db: Database, user_id: int, invitation_id: int) -> None:
    with db.conn() as connection:
        changed = connection.execute(
            "UPDATE account_invitations SET revoked_at=? WHERE id=? AND accepted_at IS NULL "
            "AND account_id IN (SELECT id FROM user_accounts WHERE owner_user_id=?)",
            (time.time(), invitation_id, user_id),
        ).rowcount
        if not changed:
            raise HTTPException(404, "Offene Einladung nicht gefunden")


def accept(db: Database, user_id: int, token: str) -> None:
    from .tenancy import user_household_guard
    # Establish membership before taking the OS lock. The merge transaction
    # re-reads both the invitation and membership after acquiring it.
    with db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _invitation(connection, token)
        _account(connection, user_id)
    with user_household_guard(db, user_id) as locked_account, db.conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        invitation = _invitation(connection, token)
        current = _account(connection, user_id)
        if int(current["id"]) != locked_account:
            raise HTTPException(409, "Dein Haushalt hat sich geändert. Bitte erneut versuchen.")
        count = connection.execute("SELECT COUNT(*) FROM account_members WHERE account_id=?", (current["id"],)).fetchone()[0]
        if current["owner_user_id"] != user_id or count > 1 or current["id"] == invitation["account_id"]:
            raise HTTPException(409, "Du gehörst bereits zu einem Konto mit mehreren Personen")
        from .tenancy import merge_households
        merge_households(connection, int(current["id"]), int(invitation["account_id"]), db=db)
        connection.execute("DELETE FROM user_accounts WHERE id=?", (current["id"],))
        _join(connection, invitation, user_id)
