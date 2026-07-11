"""Shelly Plug + externe HDD: an-/ausschalten + mounten/unmounten.

Workflow:
  power_on() -> Shelly relay/0?turn=on -> warte X Sekunden bis HDD spin-up
              -> root-eigene systemd-Path-Aktion startet die fstab-Mount-Unit

  power_off() -> systemd stoppt die freigegebene fstab-Mount-Unit
               -> warte 2s damit fs flushed
               -> Shelly relay/0?turn=off

Shelly Plug S Gen1 API:
  GET  http://<plug>/status                 -> {... "relays":[{"ison":true/false, ...}]}
  GET  http://<plug>/relay/0?turn=on/off    -> {"ison": ...}

Shelly Plug Gen2 (etwas anderes Schema, aber auch unterstützt):
  GET  http://<plug>/rpc/Switch.GetStatus?id=0
  GET  http://<plug>/rpc/Switch.Set?id=0&on=true

Beides wird unterstützt - der Code probiert zuerst Gen1, fällt auf Gen2 zurück.

Voraussetzung für mount/umount: der Mount-Punkt steht in /etc/fstab und stimmt
mit der root-eigenen Allow-Datei /etc/scrapper-hdd-mountpoint überein. Der
Webdienst selbst erhält keine sudo- oder Mount-Rechte.
"""
from __future__ import annotations

import logging
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)
_ROOT_ACTION_LOCK = threading.Lock()
_ROOT_ALLOW_FILE = Path("/etc/scrapper-hdd-mountpoint")


class HDDController:
    """Steuert eine Shelly-Plug + dahinter angeschlossene externe HDD."""

    def __init__(self, cfg: dict):
        self.enabled = bool(cfg.get("enabled", False))
        self.shelly_url = (cfg.get("shelly_url") or "").rstrip("/")
        self.mount_point = (cfg.get("mount_point") or "").rstrip("/")
        self.device = (cfg.get("device") or "").strip()  # z.B. /dev/sdb1, optional
        self.spinup_delay = int(cfg.get("spinup_delay_sec") or 12)
        self.unmount_delay = int(cfg.get("unmount_delay_sec") or 2)
        self.http_timeout = int(cfg.get("http_timeout_sec") or 8)

    # ---------- Shelly-API ----------

    def shelly_status(self) -> Optional[bool]:
        """True = relay ON, False = OFF, None = nicht erreichbar."""
        if not self.shelly_url:
            return None
        # Gen1 zuerst
        try:
            r = requests.get(f"{self.shelly_url}/status", timeout=self.http_timeout)
            r.raise_for_status()
            data = r.json()
            relays = data.get("relays")
            if isinstance(relays, list) and relays:
                return bool(relays[0].get("ison"))
        except Exception as e:
            logger.debug(f"Shelly Gen1 status fail, versuche Gen2: {e}")
        # Gen2 fallback
        try:
            r = requests.get(f"{self.shelly_url}/rpc/Switch.GetStatus",
                              params={"id": 0}, timeout=self.http_timeout)
            r.raise_for_status()
            return bool(r.json().get("output"))
        except Exception as e:
            logger.warning(f"Shelly nicht erreichbar ({self.shelly_url}): {e}")
            return None

    def shelly_switch(self, on: bool) -> bool:
        """True wenn der Schaltbefehl angekommen ist."""
        if not self.shelly_url:
            return False
        # Gen1
        try:
            r = requests.get(
                f"{self.shelly_url}/relay/0",
                params={"turn": "on" if on else "off"},
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Shelly Gen1 switch fail, versuche Gen2: {e}")
        # Gen2
        try:
            r = requests.get(
                f"{self.shelly_url}/rpc/Switch.Set",
                params={"id": 0, "on": "true" if on else "false"},
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Shelly switch fail: {e}")
            return False

    # ---------- Mount-API ----------

    def is_mounted(self) -> bool:
        if not self.mount_point:
            return False
        try:
            return os.path.ismount(self.mount_point)
        except Exception:
            return False

    def _root_mount_action(self, action: str) -> Dict:
        """Fordert genau mount/unmount über die root-eigene Path-Unit an."""
        if action not in {"mount", "unmount"}:
            return {"ok": False, "error": "Ungültige HDD-Aktion"}
        try:
            allowed = _ROOT_ALLOW_FILE.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return {
                "ok": False,
                "error": f"Root-Allow-Datei {_ROOT_ALLOW_FILE} fehlt oder ist nicht lesbar: {exc}",
            }
        try:
            allowed_path = str(Path(allowed).resolve(strict=False))
            configured_path = str(Path(self.mount_point).resolve(strict=False))
        except OSError as exc:
            return {"ok": False, "error": f"Mount-Punkt ungültig: {exc}"}
        if configured_path != allowed_path:
            return {
                "ok": False,
                "error": (
                    f"Mount-Punkt ist nicht root-freigegeben: Config={configured_path}, "
                    f"erlaubt={allowed_path}"
                ),
            }

        from ..config_store import get_config
        data_dir = Path(get_config().path).parent.resolve()
        request_path = data_dir / "hdd-action.request"
        result_path = data_dir / "hdd-action.result"
        request_id = str(uuid.uuid4())
        tmp_path = data_dir / f".hdd-action.request.{os.getpid()}.{request_id}.tmp"
        payload = {"action": action, "request_id": request_id}

        with _ROOT_ACTION_LOCK:
            try:
                result_path.unlink(missing_ok=True)
                with open(tmp_path, "x", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                tmp_path.chmod(0o600)
                tmp_path.replace(request_path)
            except OSError as exc:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return {"ok": False, "error": f"HDD-Aktion konnte nicht angefordert werden: {exc}"}

            for _ in range(2000):
                if result_path.is_file():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        result = None
                    if isinstance(result, dict) and result.get("request_id") == request_id:
                        result_path.unlink(missing_ok=True)
                        return result
                time.sleep(0.05)

            if request_path.exists():
                request_path.unlink(missing_ok=True)
                return {"ok": False, "error": "HDD-Root-Aktion wurde von systemd nicht abgeholt"}
            return {"ok": False, "error": "HDD-Root-Aktion lieferte kein Ergebnis"}

    def mount(self) -> Dict:
        if not self.mount_point:
            return {"ok": False, "error": "Kein mount_point konfiguriert"}
        if self.is_mounted():
            return {"ok": True, "already": True, "mount_point": self.mount_point}
        return self._root_mount_action("mount")

    def unmount(self) -> Dict:
        if not self.mount_point:
            return {"ok": False, "error": "Kein mount_point konfiguriert"}
        if not self.is_mounted():
            return {"ok": True, "already": True, "mount_point": self.mount_point}
        return self._root_mount_action("unmount")

    # ---------- High-Level ----------

    def status(self) -> Dict:
        """Snapshot für UI: Shelly + Mount-State + Device-Anwesenheit."""
        return {
            "enabled": self.enabled,
            "shelly_url": self.shelly_url,
            "mount_point": self.mount_point,
            "device": self.device,
            "shelly_on": self.shelly_status(),
            "mounted": self.is_mounted(),
            "device_present": (os.path.exists(self.device) if self.device else None),
        }

    def power_on_and_mount(self) -> Dict:
        """Shelly einschalten, warten, mounten."""
        if not self.enabled:
            return {"ok": False, "error": "HDD-Control nicht aktiviert in Config"}

        steps = []
        # Wenn schon an + gemounted, no-op
        if self.is_mounted():
            return {"ok": True, "skipped": True, "reason": "bereits gemounted"}

        was_on = self.shelly_status()
        if was_on is False:
            logger.info("Shelly einschalten...")
            ok = self.shelly_switch(True)
            steps.append({"step": "shelly_on", "ok": ok})
            if not ok:
                return {"ok": False, "error": "Shelly switch fail", "steps": steps}
            logger.info(f"Spinup-delay {self.spinup_delay}s")
            time.sleep(self.spinup_delay)
        elif was_on is None:
            steps.append({"step": "shelly_status", "ok": False, "note": "nicht erreichbar - überspringen"})
        else:
            steps.append({"step": "shelly_on", "ok": True, "already": True})

        # Mounten
        m = self.mount()
        steps.append({"step": "mount", **m})
        if not m.get("ok"):
            return {"ok": False, "error": "Mount fail", "steps": steps}

        return {"ok": True, "steps": steps, "status": self.status()}

    def unmount_and_power_off(self) -> Dict:
        """Unmounten, warten, Shelly aus."""
        if not self.enabled:
            return {"ok": False, "error": "HDD-Control nicht aktiviert in Config"}

        steps = []
        # Unmount
        if self.is_mounted():
            u = self.unmount()
            steps.append({"step": "umount", **u})
            if not u.get("ok"):
                return {"ok": False, "error": "Umount fail (FS busy?)", "steps": steps}
            # Kurz warten damit FS-Buffer flushen können
            time.sleep(self.unmount_delay)
        else:
            steps.append({"step": "umount", "ok": True, "already": True})

        # Shelly aus
        was_on = self.shelly_status()
        if was_on is True:
            ok = self.shelly_switch(False)
            steps.append({"step": "shelly_off", "ok": ok})
            if not ok:
                return {"ok": False, "error": "Shelly switch fail", "steps": steps}
        elif was_on is False:
            steps.append({"step": "shelly_off", "ok": True, "already": True})
        else:
            steps.append({"step": "shelly_off", "ok": False, "note": "Shelly nicht erreichbar"})

        return {"ok": True, "steps": steps, "status": self.status()}


def get_controller() -> HDDController:
    from ..config_store import get_config
    cfg = get_config().get("external_hdd", default={}) or {}
    return HDDController(cfg)
