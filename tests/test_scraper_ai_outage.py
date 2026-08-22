"""Ein OpenAI-Ausfall darf KI-freie Social-Links nicht blockieren."""

from types import SimpleNamespace

from app.jobs.scraper import ScraperJob, reset_cancel


class _Db:
    def pending_list(self, _status):
        return []

    def history_has(self, _url):
        return False

    def download_failures_retry_candidates(self, _limit):
        return []

    def pending_count(self):
        return 1


class _Router:
    def fetch_all_with_attachments(self):
        return {
            "urls": [{
                "url": "https://www.tiktok.com/@koch/video/123",
                "type": "recipe",
                "source_account": None,
                "mail_uid": None,
            }],
            "attachments": [],
        }

    def delete_processed_mails(self, _uids):
        return 0


class _Config:
    def get(self, _section, _key, default=None):
        return default


def test_link_only_run_continues_when_analyzer_health_is_false(monkeypatch):
    from app.core import webhook

    reset_cancel()
    monkeypatch.setattr(webhook, "notify", lambda *_args, **_kwargs: None)
    job = object.__new__(ScraperJob)
    job.analyzer_enabled = True
    job.analyzer = SimpleNamespace(model="gpt-test", health=lambda: False)
    job.router = _Router()
    job.db = _Db()
    job.cfg = _Config()
    job.process_url = lambda _item: {"status": "pending"}

    summary = job.run()

    assert summary["ai_available"] is False
    assert summary["new"] == 1
    assert summary["recipe_pending"] == 1
    assert summary["errors"] == 0

