"""Shelly Plug + externe HDD: an-/ausschalten + mounten/unmounten.

Workflow:
  power_on() -> Shelly relay/0?turn=on -> warte X Sekunden bis HDD spin-up
              -> /bin/mount <mount_point> (via sudo, sudoers-Rule nötig)

  power_off() -> /bin/umount <mount_point>
               -> warte 2s damit fs flushed
               -> Shelly relay/0?turn=off

Shelly Plug S Gen1 API:
  GET  http://<plug>/status                 -> {... "relays":[{"ison":true/false, ...}]}
  GET  http://<plug>/relay/0?turn=on/off    -> {"ison": ...}

Shelly Plug Gen2 (etwas anderes Schema, aber auch unterstützt):
  GET  http://<plug>/rpc/Switch.GetStatus?id=0
  GET  http://<plug>/rpc/Switch.Set?id=0&on=true

Beides wird unterstützt - der Code probiert zuerst Gen1, fällt auf Gen2 zurück.

Voraussetzung für mount/umount: scrapper-User braucht NOPASSWD-Sudo für genau
die zwei Commands. Siehe systemd/sudoers-scrapper-hdd.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Dict, Optional

from .webhook import server_configured_request

logger = logging.getLogger(__name__)


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

    def _shelly_request(self, path: str, *, params: dict | None = None):
        if not self.shelly_url:
            raise ValueError("Keine Shelly-URL konfiguriert")
        return server_configured_request(
            "GET",
            f"{self.shelly_url}/{path.lstrip('/')}",
            trusted_private_bases=(self.shelly_url,),
            params=params,
            timeout=self.http_timeout,
        )

    def shelly_status(self) -> Optional[bool]:
        """True = relay ON, False = OFF, None = nicht erreichbar."""
        if not self.shelly_url:
            return None
        # Gen1 zuerst
        try:
            r = self._shelly_request("status")
            r.raise_for_status()
            data = r.json()
            relays = data.get("relays")
            if isinstance(relays, list) and relays:
                return bool(relays[0].get("ison"))
        except Exception as e:
            logger.debug(f"Shelly Gen1 status fail, versuche Gen2: {e}")
        # Gen2 fallback
        try:
            r = self._shelly_request("rpc/Switch.GetStatus", params={"id": 0})
            r.raise_for_status()
            return bool(r.json().get("output"))
        except Exception as e:
            logger.warning("Shelly nicht erreichbar: %s", type(e).__name__)
            return None

    def shelly_switch(self, on: bool) -> bool:
        """True wenn der Schaltbefehl angekommen ist."""
        if not self.shelly_url:
            return False
        # Gen1
        try:
            r = self._shelly_request(
                "relay/0",
                params={"turn": "on" if on else "off"},
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Shelly Gen1 switch fail, versuche Gen2: {e}")
        # Gen2
        try:
            r = self._shelly_request(
                "rpc/Switch.Set",
                params={"id": 0, "on": "true" if on else "false"},
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

    def _run_sudo(self, cmd: list, timeout: int = 30) -> Dict:
        """Führt sudo-Kommando aus. Returnt {ok, stdout, stderr, returncode}."""
        try:
            full = ["sudo", "-n"] + cmd   # -n = niemals nach Passwort fragen
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            return {
                "ok": r.returncode == 0,
                "returncode": r.returncode,
                "stdout": r.stdout[-2000:],
                "stderr": r.stderr[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "returncode": -1, "stderr": "Timeout"}
        except Exception as e:
            return {"ok": False, "returncode": -1, "stderr": str(e)}

    def mount(self) -> Dict:
        if not self.mount_point:
            return {"ok": False, "error": "Kein mount_point konfiguriert"}
        if self.is_mounted():
            return {"ok": True, "already": True, "mount_point": self.mount_point}
        return self._run_sudo(["/bin/mount", self.mount_point])

    def unmount(self) -> Dict:
        if not self.mount_point:
            return {"ok": False, "error": "Kein mount_point konfiguriert"}
        if not self.is_mounted():
            return {"ok": True, "already": True, "mount_point": self.mount_point}
        return self._run_sudo(["/bin/umount", self.mount_point])

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
