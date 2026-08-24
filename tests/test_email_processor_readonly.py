from app.core import email_processor


def test_readonly_mail_scan_uses_examine_and_body_peek(monkeypatch):
    raw_message = (
        b"Subject: Rezept\r\n"
        b"Message-ID: <one@example.test>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"https://www.tiktok.com/@koch/video/123?share=1\r\n"
    )

    class FakeImap:
        def __init__(self, *_args, **_kwargs):
            self.select_calls = []
            self.fetch_calls = []

        def login(self, _username, _password):
            return "OK", []

        def select(self, folder, readonly=False):
            self.select_calls.append((folder, readonly))
            return "OK", [b"1"]

        def search(self, *_args):
            return "OK", [b"1"]

        def fetch(self, mail_id, query):
            self.fetch_calls.append((mail_id, query))
            return "OK", [(b"1 BODY[]", raw_message)]

        def logout(self):
            return "BYE", []

    fake = FakeImap()
    monkeypatch.setattr(email_processor.imaplib, "IMAP4_SSL", lambda *_a, **_kw: fake)
    account = email_processor.MailAccount(
        "recipe",
        {
            "enabled": True,
            "username": "user@example.test",
            "password": "secret",
            "folder": "INBOX",
        },
        "recipe",
    )

    result = account.fetch_all_readonly(max_mails=50, include_attachments=False)

    assert fake.select_calls == [("INBOX", True)]
    assert fake.fetch_calls == [(b"1", "(BODY.PEEK[])")]
    assert result["urls"][0]["url"] == "https://www.tiktok.com/@koch/video/123"
    assert result["attachments"] == []
