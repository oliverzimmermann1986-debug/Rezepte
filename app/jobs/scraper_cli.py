"""CLI-Wrapper für den Scraper-Job (von systemd aufgerufen)."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from ..config_store import get_config
from ..db import get_db
from .locks import file_lock_or_none
from .scraper import run_job


def main() -> int:
    log_dir = Path(get_config().get("paths", "logs_dir", default="/opt/scrapper/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"scraper-{datetime.now():%Y%m%d-%H%M%S}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("scraper_cli")

    # Vor dem job_start den File-Lock probieren - wenn der Web-Trigger
    # gerade den Scraper laufen lässt, sauber rausgehen, nicht doppelt
    # IMAP-Login + Telegram-Spam machen.
    with file_lock_or_none("scraper") as flock:
        if flock is None:
            logger.info("Scraper-Job (CLI): anderer Prozess hält den Lock - skip")
            return 0  # exit 0 - kein Fehler, nur "nicht jetzt"

        db = get_db()
        job_id = db.job_start("scraper", log_file=str(log_file))
        logger.info(f"Scraper-Job gestartet (ID={job_id}, via CLI)")

        try:
            summary = run_job()
            db.job_finish(job_id, "ok", summary)
            logger.info(f"OK: {json.dumps(summary, ensure_ascii=False)}")
            return 0
        except Exception as e:
            logger.exception("Scraper-Job fehlgeschlagen")
            db.job_finish(job_id, "error", {"error": str(e)})
            return 1


if __name__ == "__main__":
    sys.exit(main())
