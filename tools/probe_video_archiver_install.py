"""Linux-only: real venv launchers; fake package/service boundary; no real URLs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

def main():
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Linux root required; operates only in temporary directories.")
    if len(sys.argv) > 1:
        payload = json.loads(Path(sys.argv[1]).read_text())
    else:
        project = Path(__file__).resolve().parents[1]
        files = [*project.joinpath("video_archiver").glob("*.py"), project/"video_archiver/install-host.sh", project/"video_archiver/requirements.txt", project/"systemd/video-archiver.service", project/"systemd/video-archiver.timer"]
        payload = {p.relative_to(project).as_posix(): p.read_text() for p in files}
    allowed = {"video_archiver/__init__.py", "video_archiver/__main__.py", "video_archiver/worker.py",
               "video_archiver/install-host.sh", "video_archiver/requirements.txt",
               "systemd/video-archiver.service", "systemd/video-archiver.timer"}
    if set(payload) != allowed or not all(isinstance(value, str) for value in payload.values()):
        raise SystemExit("Probe requires exactly the known Archiver source and unit files.")
    results = {}
    with tempfile.TemporaryDirectory(prefix='archiver-install-probe-') as temporary:
        root = Path(temporary)
        source = root / 'source'
        for name, content in payload.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        install = root / 'installed'
        state = root / 'state'
        archive = root / 'archive'
        units = root / 'units'
        fake_bin = root / 'fake-bin'
        fake_bin.mkdir()
        def executable(name, content):
            path = fake_bin / name
            # Templates acquired one indentation level when moved into main;
            # explicit newlines inside printf formats must remain untouched.
            path.write_text(content.replace('\n    ', '\n'))
            path.chmod(0o755)
            return path
        executable('python3', '''#!/bin/bash
    set -e
    if [[ "$1" == "-m" && "$2" == "venv" ]]; then
      /usr/bin/python3 -m venv --without-pip "$3"
      cat > "$3/bin/pip" <<'PIP'
    #!/bin/bash
    set -e
    env_dir="$(dirname "$(dirname "$(realpath "$0")")")"
    printf '#!%s/bin/python3\nimport sys\nprint("isolated-yt-dlp")\n' "$env_dir" > "$env_dir/bin/yt-dlp"
    chmod 755 "$env_dir/bin/yt-dlp"
    PIP
      chmod 755 "$3/bin/pip"
    else
      exec /usr/bin/python3 "$@"
    fi
    ''')
        executable('ffmpeg', '#!/bin/sh\nexit 0\n')
        executable('sudo', '#!/bin/sh\nshift 2\nexec "$@"\n')
        executable('systemctl', '''#!/bin/bash
    printf '%s\\n' "$*" >> "$FAKE_SYSTEMD_LOG"
    if [[ "$1" == "is-active" ]]; then
      [[ "$FAKE_TIMER_ACTIVE" == "1" ]]
    elif [[ "$1" == "daemon-reload" && -f "$FAKE_FAIL_RELOAD" ]]; then
      rm -- "$FAKE_FAIL_RELOAD"
      exit 23
    fi
    ''')
        log = root / 'systemd.log'
        fail_reload = root / 'fail-reload'
        env = dict(os.environ, PATH=str(fake_bin) + ':' + os.environ['PATH'],
                   VIDEO_ARCHIVER_INSTALL_DIR=str(install), VIDEO_ARCHIVER_STATE_DIR=str(state),
                   VIDEO_ARCHIVER_ARCHIVE_DIR=str(archive), VIDEO_ARCHIVER_UNIT_DIR=str(units),
                   VIDEO_ARCHIVER_USER='root', FAKE_SYSTEMD_LOG=str(log), FAKE_TIMER_ACTIVE='0',
                   FAKE_FAIL_RELOAD=str(fail_reload))
        installer = source / 'video_archiver/install-host.sh'
        subprocess.run(['bash', '-n', str(installer)], check=True)
        subprocess.run(['bash', str(installer)], env=env, capture_output=True, text=True, check=True, timeout=60)
        results['permanent_console_launcher'] = subprocess.run([str(install/'venv/bin/yt-dlp'), '--version'], capture_output=True).returncode == 0
        first_prefix = (install/'venv').resolve()
        assert first_prefix.name.startswith('.venv.')
        results['installer_never_starts_download_service'] = 'start video-archiver.service' not in log.read_text() and 'start --' not in log.read_text()
        sys.path.insert(0, str(install))
        from video_archiver import ArchiveQueue
        queue = ArchiveQueue(state/'queue.db')
        queue.enqueue(99, 'https://www.tiktok.com/@synthetic/video/99')
        queue.claim()
        queue.fail(99, 'synthetic deleted URL', max_attempts=1)
        custom_service = (units/'video-archiver.service').read_text().replace('--max-jobs 4', '--max-jobs 8')
        custom_service = custom_service.replace('[Service]', '[Service]\nEnvironment="ARCHIVER_CUSTOM_SETTING=preserve"')
        (units/'video-archiver.service').write_text(custom_service)
        custom_timer = (units/'video-archiver.timer').read_text().replace('OnUnitActiveSec=5min', 'OnUnitActiveSec=7min')
        (units/'video-archiver.timer').write_text(custom_timer)
        log.write_text('')
        subprocess.run(['bash', str(installer)], env=env, capture_output=True, text=True, check=True, timeout=60)
        updated_service = (units/'video-archiver.service').read_text()
        results['custom_worker_settings_preserved'] = '--max-jobs 8' in updated_service and '--timeout 900' in updated_service and 'ARCHIVER_CUSTOM_SETTING=preserve' in updated_service
        results['custom_timing_budget_covers_jobs'] = 'TimeoutStartSec=7500' in updated_service
        results['custom_timer_schedule_preserved'] = (units/'video-archiver.timer').read_text() == custom_timer
        results['failed_job_does_not_roll_back_install'] = queue.get(99)['status'] == 'failed' and subprocess.run([str(install/'venv/bin/yt-dlp'), '--version'], capture_output=True).returncode == 0
        results['disabled_timer_preserved'] = 'start video-archiver.timer' not in log.read_text() and 'enable' not in log.read_text()
        good_prefix = (install/'venv').resolve()
        marker = install/'video_archiver/previous-install-sentinel'
        marker.write_text('old version')
        requirements = install/'requirements.txt'
        requirements.write_text('old requirements sentinel')
        good_units = {name: (units/name).read_bytes() for name in ['video-archiver.service', 'video-archiver.timer']}
        log.write_text('')
        fail_reload.touch()
        env['FAKE_TIMER_ACTIVE'] = '1'
        failed = subprocess.run(['bash', str(installer)], env=env, capture_output=True, text=True, timeout=60)
        results['rollback_code_and_venv'] = failed.returncode == 23 and marker.read_text() == 'old version' and (install/'venv').resolve() == good_prefix
        results['rollback_requirements_and_units'] = requirements.read_text() == 'old requirements sentinel' and all((units/name).read_bytes() == content for name, content in good_units.items())
        results['rollback_restores_active_timer'] = 'start video-archiver.timer' in log.read_text()
        # Demonstrate why moving a venv is invalid, using the same real shebang.
        moved_prefix = root/'moved-venv'
        original_prefix = root/'original-venv'
        subprocess.run([str(fake_bin/'python3'), '-m', 'venv', str(original_prefix)], env=env, check=True)
        subprocess.run([str(original_prefix/'bin/pip'), 'install'], check=True)
        original_prefix.rename(moved_prefix)
        try:
            subprocess.run([str(moved_prefix/'bin/yt-dlp'), '--version'], capture_output=True)
            results['old_moved_venv_reproduces_failure'] = False
        except FileNotFoundError:
            results['old_moved_venv_reproduces_failure'] = True
        # A real SIGTERM against a synthetic CLI download cleans its temporary files.
        queue.enqueue(100, 'https://www.tiktok.com/@synthetic/video/100')
        downloader = executable('waiting-downloader', '#!/usr/bin/python3\nimport os, pathlib, sys, time\np=pathlib.Path(sys.argv[sys.argv.index("--output")+1].replace("%(ext)s", "part"))\np.write_bytes(b"partial")\np.with_name("ready").write_text(str(os.getpid()))\ntime.sleep(60)\n')
        child = subprocess.Popen([str(install/'venv/bin/python'), '-m', 'video_archiver', '--queue', str(state/'queue.db'),
            'run', '--archive', str(archive), '--yt-dlp', str(downloader), '--confirm-rights', '--min-free-mb', '0'],
            cwd=install, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+10
            while time.monotonic() < deadline and not list((archive/'.work').glob('*/ready')):
                time.sleep(0.02)
            ready = list((archive/'.work').glob('*/ready'))
            assert ready, 'synthetic worker did not start'
            downloader_pid = int(ready[0].read_text())
            child.send_signal(signal.SIGTERM)
            child.communicate(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        results['sigterm_cleans_partial_files'] = child.returncode == 130 and not list((archive/'.work').iterdir())
        results['sigterm_requeues_job'] = queue.get(100)['status'] == 'queued'
        try:
            os.kill(downloader_pid, 0)
            results['sigterm_stops_downloader'] = False
        except ProcessLookupError:
            results['sigterm_stops_downloader'] = True
        unit = payload['systemd/video-archiver.service']
        results['unit_timing_budget_and_handled_failures'] = 'TimeoutStartSec=3900' in unit and 'SuccessExitStatus=1' in unit and '--max-jobs 4' in unit and '--timeout 900' in unit
    assert all(results.values()), results
    print(json.dumps(results))


if __name__ == "__main__":
    main()
