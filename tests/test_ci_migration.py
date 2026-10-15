"""Moving tests to VM 110 must preserve automatic requests and release blocking."""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def dispatcher(responses):
    document = workflow("quality.yml")
    script = document["jobs"]["request"]["steps"][0]["run"].split("<<'NODE'\n", 1)[1].rsplit("\nNODE", 1)[0]
    stub = "const responses=" + json.dumps(responses) + "; const calls=[]; globalThis.fetch=async(url,options)=>{ calls.push({url,method:options.method,body:options.body?JSON.parse(options.body):null}); const r=responses.shift(); if(!r) throw Error('unexpected API request'); return {status:r.status,json:async()=>r.data}; };\n"
    return subprocess.run(["node", "--input-type=module", "-e", stub + script + "\nconsole.log('RESULT '+JSON.stringify(calls));"],
                          capture_output=True, text=True, timeout=10,
                          env={**os.environ, "GITHUB_REPOSITORY": "owner/Rezepte", "PRUEFDIENST_SOURCE_SHA": SHA,
                               "GH_TOKEN": "synthetic-never-print-token"})


def ref(status=200, sha=SHA):
    return {"status": status, "data": {"object": {"type": "commit", "sha": sha}}}


def test_dispatches_exact_sha_without_checkout_or_executing_repository_code():
    document = workflow("quality.yml")
    triggers = document.get("on", document.get(True))
    assert triggers["push"]["branches"] == ["**", "!main"]
    assert "pull_request" in triggers
    job = document["jobs"]["request"]
    assert "pull_request.head.sha" in job["env"]["PRUEFDIENST_SOURCE_SHA"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert all("uses" not in step for step in job["steps"])
    result = dispatcher([{"status": 404, "data": {}}, ref(201)])
    assert result.returncode == 0, result.stderr
    calls = json.loads(result.stdout.split("RESULT ")[1])
    assert [call["method"] for call in calls] == ["GET", "POST"]
    assert calls[-1]["body"] == {"ref": "refs/tags/ci/rezepte-request/" + SHA, "sha": SHA}
    assert "synthetic-never-print-token" not in result.stdout + result.stderr


def test_existing_request_is_idempotent_and_concurrent_creation_is_safe():
    result = dispatcher([ref()])
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout.split("RESULT ")[1])) == 1
    result = dispatcher([{"status": 404, "data": {}}, {"status": 422, "data": {}}, ref()])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("responses", [[ref(sha="b" * 40)], [{"status": 403, "data": {}}],
                                     [{"status": 404, "data": {}}, ref(201, sha="b" * 40)]])
def test_api_denial_and_wrong_ref_never_request_or_move_unchecked_code(responses):
    result = dispatcher(responses)
    assert result.returncode != 0
    assert "Requested VM 110" not in result.stdout
    assert "synthetic-never-print-token" not in result.stdout + result.stderr


def test_signed_uploads_require_canonical_pruefdienst_status_before_signing():
    legacy = workflow("ios.yml")
    assert all(str(job["runs-on"]).startswith("macos-") for job in legacy["jobs"].values())
    assert legacy["jobs"]["testflight"]["needs"] == "verify"
    steps = legacy["jobs"]["verify"]["steps"]
    assert any("pruefdienst-status.mjs" in step.get("run", "") for step in steps)
    native = workflow("ios-swift.yml")
    triggers = native.get("on", native.get(True))
    assert "**" in triggers["push"]["branches"]
    assert "ci/**" in triggers["push"]["tags-ignore"]
    steps = native["jobs"]["testflight"]["steps"]
    gate = next(i for i, step in enumerate(steps) if "pruefdienst-status.mjs" in step.get("run", ""))
    signing = next(i for i, step in enumerate(steps) if "signing" in step.get("name", "").lower() or "keychain" in step.get("name", "").lower())
    assert gate < signing
    for document in (legacy, native):
        assert document["permissions"]["statuses"] == "read"
