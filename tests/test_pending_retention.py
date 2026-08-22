"""Alte manuelle Eingänge dürfen nicht erneut importiert werden."""

import time


def test_auto_skip_records_history_atomically(test_db):
    url = "https://www.instagram.com/reel/Alt123/"
    test_db.pending_add(
        url,
        "recipe",
        description="unvollständig",
        ai_suggestion={"name": "Alter manueller Eingang"},
    )
    with test_db.conn() as connection:
        connection.execute(
            "UPDATE pending SET created_at=? WHERE url=?",
            (time.time() - 31 * 86400, url),
        )

    assert test_db.auto_skip_old_pending(days=30) == 1
    assert test_db.history_has(url) is True
    history = test_db.history_get(url)
    assert history["content_type"] == "recipe"
    assert history["name"] == "Alter manueller Eingang"
    with test_db.conn() as connection:
        status = connection.execute(
            "SELECT status FROM pending WHERE url=?", (url,)
        ).fetchone()[0]
    assert status == "auto_skipped"
    assert test_db.auto_skip_old_pending(days=30) == 0

