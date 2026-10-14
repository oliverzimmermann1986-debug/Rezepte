"""Full Linux CI plan shared by Proxmox and GitHub; never runs a deployment.

Reports use the central Pruefdienst gate/marker contract. Every planned gate
starts as not_run and is persisted before execution; missing tools, empty test
suites and unexpected skips cannot produce a green result.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path("reports/unified-quality-report.json")
ALLOWED_SKIPS = {
    "tests.test_video_archiver_install": "isolated Linux installer ownership test requires root",
    "tests.test_audit_load_auth": "origin/main has advanced beyond the audited schema 260",
}


def junit_summary(path: Path) -> dict:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        raise ValueError("No tests in JUnit report")
    skipped, failed, unexpected = [], [], []
    for case in cases:
        name = case.get("classname", "") + "." + case.get("name", "")
        skip = case.find("skipped")
        if skip is not None:
            reason = skip.get("message", "")
            skipped.append({"name": name, "reason": reason})
            if ALLOWED_SKIPS.get(case.get("classname")) != reason:
                unexpected.append(name)
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(name)
    passed = len(cases) - len(skipped) - len(failed)
    if passed <= 0 or unexpected:
        raise ValueError(f"Test suite incomplete: {passed} passed; unexpected skips: {unexpected}")
    return {"total": len(cases), "passed": passed, "failed": failed, "skipped": skipped}


def node_summary(output: str) -> dict:
    def count(key):
        match = re.search(rf"^# {key} (\d+)\s*$", output, re.M)
        if not match:
            raise ValueError(f"Node test summary missing: {key}")
        return int(match[1])
    result = {key: count(key) for key in ("tests", "pass", "fail", "cancelled", "skipped", "todo")}
    if result["tests"] <= 0 or result["pass"] <= 0 or any(result[k] for k in ("fail", "cancelled", "skipped", "todo")):
        raise ValueError(f"Node suite incomplete: {result}")
    return result


class Plan:
    def __init__(self, root: Path, ids: list[str]):
        self.root = root
        self.directory = root / "reports/ci"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.report = {"schemaVersion": 1, "profile": "rezepte", "startedAt": time.time(),
                       "sha": os.environ.get("PRUEFDIENST_SHA") or os.environ.get("GITHUB_SHA"),
                       "gates": [{"id": key, "status": "not_run", "durationMs": 0} for key in ids],
                       "boundaries": ["Synthetic databases only; no production server or real AI requests",
                                      "Swift/Xcode tests and signed iOS builds require the macOS workflow",
                                      "Root-only installer ownership test remains skipped in unprivileged CI"]}
        self.env = {**os.environ, "CI": "true", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
                    "EXPO_NO_TELEMETRY": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        for key in ("OPENAI_API_KEY", "SCRAPPER_CONFIG", "SCRAPPER_LEGAL_CONFIG_FILE"):
            self.env.pop(key, None)
        self.save()
        print("[pruefdienst-plan] " + json.dumps(ids), flush=True)

    def save(self):
        target = self.root / REPORT
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def gate(self, key: str, action, requires: tuple[str, ...] = ()):
        gate = next(g for g in self.report["gates"] if g["id"] == key)
        if any(next(g for g in self.report["gates"] if g["id"] == dep)["status"] != "passed" for dep in requires):
            gate["reason"] = "Prerequisite failed: " + ", ".join(requires)
        else:
            started = time.monotonic()
            print(f"Running gate: {key}", flush=True)
            try:
                details = action()
                gate.update(status="passed", **(details or {}))
            except Exception as exc:
                gate.update(status="failed", reason=str(exc)[:2000])
                print(f"FAILED {key}: {gate['reason']}", flush=True)
            gate["durationMs"] = round((time.monotonic() - started) * 1000)
        self.save()
        marker = {k: gate[k] for k in ("id", "status", "durationMs")}
        print("[pruefdienst-gate] " + json.dumps(marker), flush=True)

    def command(self, key: str, args: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> str:
        log = self.directory / f"{key}.log"
        with log.open("wb") as stream:
            process = subprocess.Popen(args, cwd=cwd or self.root, env=self.env,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=os.name == "posix")
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                raise RuntimeError(f"{key}: timed out after {timeout}s")
        output = log.read_text(encoding="utf-8", errors="replace")
        print(output[-60000:] if code else output[-4000:], flush=True)
        if code:
            raise RuntimeError(f"{key}: exit {code}; see reports/ci/{key}.log")
        return output


def verify_runtime(plan: Plan):
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Python 3.11 required")
    node = plan.command("runtime", ["node", "--version"]).strip()
    if node != "v20.19.4":
        raise ValueError("Node 20.19.4 required; found " + node)
    return {"python": sys.version.split()[0], "node": node}


def javascript_syntax(plan: Plan):
    files = sorted((plan.root / "app/static").rglob("*.js"))
    files += sorted((plan.root / "tools").rglob("*.cjs"))
    if not files:
        raise ValueError("No JavaScript sources found")
    # One log for the whole stage; node --check never evaluates application code.
    script = "for (const f of process.argv.slice(1)) { const r = require('node:child_process').spawnSync(process.execPath, ['--check', f], {stdio:'inherit'}); if (r.status !== 0) process.exit(1); }"
    plan.command("javascript-syntax", ["node", "-e", script, *[str(p) for p in files]])
    return {"files": len(files)}


def python_tests(plan: Plan, key: str, browser_files: list[Path]):
    junit = plan.directory / f"{key}.xml"
    args = [sys.executable, "tools/ci_pytest.py", "-q", "--cov=app", "--cov=video_archiver",
            "--cov-report=", f"--junitxml={junit}"]
    if key == "client-e2e":
        args += ["--cov-append", *[str(p.relative_to(plan.root)) for p in browser_files]]
    else:
        args += [f"--ignore={p.relative_to(plan.root)}" for p in browser_files]
    plan.command(key, args)
    summary = junit_summary(junit)
    if summary["failed"]:
        raise ValueError("Failed JUnit tests: " + ", ".join(summary["failed"]))
    return {"tests": summary}


def main(argv=None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-deps", action="store_true")
    options = parser.parse_args(argv)
    browser_files = sorted(p for p in (root / "tests").rglob("test_*.py")
                           if "playwright.sync_api" in p.read_text(encoding="utf-8"))
    node_files = sorted((root / "tests").rglob("*.test.cjs"))
    node_files += sorted((root / "native-ios/tests").glob("*.test.cjs"))
    node_files += sorted((root / "native-ios/scripts").glob("*.test.mjs"))
    ids = ["runtime"] + (["python-dependencies"] if options.install_deps else [])
    ids += ["python-syntax", "python-lint", "javascript-syntax", "client-unit", "native-install",
            "native-typecheck", "native-lint", "native-config", "native-bundle", "server-unit"]
    if browser_files:
        ids.append("client-e2e")
    ids.append("coverage")
    plan = Plan(root, ids)
    plan.report["inventory"] = {"pythonTestFiles": len(list((root / "tests").rglob("test_*.py"))),
                                "browserTestFiles": [p.relative_to(root).as_posix() for p in browser_files],
                                "nodeTestFiles": [p.relative_to(root).as_posix() for p in node_files]}
    plan.save()
    plan.gate("runtime", lambda: verify_runtime(plan))
    if options.install_deps:
        plan.gate("python-dependencies", lambda: plan.command("python-dependencies", [
            sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"]) and {}, ("runtime",))
    python_requires = ("runtime", "python-dependencies") if options.install_deps else ("runtime",)
    sources = ["app", "tests", "tools", "video_archiver"]
    plan.gate("python-syntax", lambda: plan.command("python-syntax", [sys.executable, "-m", "compileall", "-q", *sources]) and {}, python_requires)
    plan.gate("python-lint", lambda: plan.command("python-lint", [sys.executable, "-m", "ruff", "check", *sources]) and {}, python_requires)
    plan.gate("javascript-syntax", lambda: javascript_syntax(plan), ("runtime",))

    def node_tests():
        if not node_files:
            raise ValueError("No Node test files found")
        output = plan.command("client-unit", ["node", "--test", "--test-reporter=tap", *map(str, node_files)])
        return {"tests": node_summary(output)}
    plan.gate("client-unit", node_tests, ("runtime",))
    native = root / "native-ios"
    plan.gate("native-install", lambda: plan.command("native-install", ["npm", "ci", "--no-audit", "--no-fund"], cwd=native) and {}, ("runtime",))
    for key, args in (
        ("native-typecheck", ["npm", "run", "typecheck"]),
        ("native-lint", ["npm", "run", "lint"]),
        ("native-config", ["npx", "--no-install", "expo", "config", "--type", "public"]),
        ("native-bundle", ["npx", "--no-install", "expo", "export", "--platform", "ios", "--output-dir", str(plan.directory / "expo-ios")]),
    ):
        plan.gate(key, lambda key=key, args=args: plan.command(key, args, cwd=native) and {}, ("native-install",))
    plan.gate("server-unit", lambda: python_tests(plan, "server-unit", browser_files), python_requires)
    if browser_files:
        plan.gate("client-e2e", lambda: python_tests(plan, "client-e2e", browser_files), python_requires)
    plan.gate("coverage", lambda: plan.command("coverage", [sys.executable, "-m", "coverage", "report", "--fail-under=48"]) and {}, python_requires)
    plan.report["finishedAt"] = time.time()
    plan.save()
    failed = [g["id"] for g in plan.report["gates"] if g["status"] != "passed"]
    print("CI: " + ("FAILED: " + ", ".join(failed) if failed else "all gates passed"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
