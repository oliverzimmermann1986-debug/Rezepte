"""CI must reject empty/incomplete suites and preserve failures."""
import json
from pathlib import Path
import sys

import pytest

from tools.ci_verify import Plan, junit_summary, node_summary


def test_failed_and_blocked_gates_are_persisted_and_later_gates_still_run(tmp_path, capsys):
    plan = Plan(tmp_path, ["broken", "blocked", "independent"])

    def broken():
        raise RuntimeError("synthetic error")

    plan.gate("broken", broken)
    plan.gate("blocked", lambda: pytest.fail("must not execute"), ("broken",))
    plan.gate("independent", lambda: {"tests": 4})
    report = json.loads((tmp_path / "reports/unified-quality-report.json").read_text())
    assert [g["status"] for g in report["gates"]] == ["failed", "not_run", "passed"]
    assert report["gates"][2]["tests"] == 4
    output = capsys.readouterr().out
    assert '[pruefdienst-plan] ["broken", "blocked", "independent"]' in output
    assert '"status": "not_run"' in output


@pytest.mark.parametrize("xml", ["<testsuites/>", '<testsuite><testcase name="a"><skipped message="dependency missing"/></testcase></testsuite>'])
def test_junit_rejects_empty_or_skipped_suite(tmp_path, xml):
    target = tmp_path / "test.xml"
    target.write_text(xml)
    with pytest.raises(ValueError):
        junit_summary(target)


def test_known_privilege_boundary_is_visible_and_not_counted_as_passed(tmp_path):
    target = tmp_path / "test.xml"
    target.write_text('<testsuite><testcase classname="tests.test_db" name="migration"/>'
                      '<testcase classname="tests.test_video_archiver_install" name="installer">'
                      '<skipped message="isolated Linux installer ownership test requires root"/>'
                      '</testcase></testsuite>')
    result = junit_summary(target)
    assert result["total"] == 2 and result["passed"] == 1
    assert len(result["skipped"]) == 1


@pytest.mark.parametrize("output", ["", "# tests 0\n# pass 0\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n",
                                    "# tests 2\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 1\n# todo 0\n"])
def test_node_rejects_missing_empty_or_skipped_suite(output):
    with pytest.raises(ValueError):
        node_summary(output)


def test_child_failure_and_timeout_never_pass(tmp_path):
    plan = Plan(tmp_path, ["exit", "timeout"])
    plan.gate("exit", lambda: plan.command("exit", [sys.executable, "-c", "raise SystemExit(7)"]))
    plan.gate("timeout", lambda: plan.command("timeout", [sys.executable, "-c", "import time; time.sleep(10)"], timeout=1))
    assert all(g["status"] == "failed" for g in plan.report["gates"])
    assert "exit 7" in plan.report["gates"][0]["reason"]
    assert "timed out" in plan.report["gates"][1]["reason"]


def test_profile_is_opt_in_without_database_or_deployment_commands():
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root / ".pruefdienst.json").read_text())
    assert profile == {"version": 1, "image": "rezepte", "command": "bash tools/ci/verify.sh",
                       "timeoutSeconds": 7200, "postgres": False}
    assert 'USER 1000:1000' in (root / "tools/ci/Dockerfile").read_text()
