"""HDD-Control: Shelly Plug ein/aus + Mount/Unmount der externen Platte."""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_admin
from ..core.hdd_controller import get_controller

router = APIRouter(prefix="/api/hdd", tags=["hdd"], dependencies=[Depends(require_admin)])


@router.get("/status")
def status() -> Dict:
    return get_controller().status()


@router.post("/power-on")
def power_on() -> Dict:
    """Shelly an + warten + mounten. Synchron (8-15 Sekunden je nach spinup_delay)."""
    return get_controller().power_on_and_mount()


@router.post("/power-off")
def power_off() -> Dict:
    """Unmount + warten + Shelly aus."""
    return get_controller().unmount_and_power_off()


@router.post("/shelly-toggle")
def shelly_toggle() -> Dict:
    """Nur das Shelly togglen ohne Mount-Aktion. Für Debug oder wenn die Platte
    extern (nicht via fstab) verwaltet wird."""
    ctl = get_controller()
    cur = ctl.shelly_status()
    if cur is None:
        raise HTTPException(503, "Shelly nicht erreichbar")
    ok = ctl.shelly_switch(not cur)
    return {"ok": ok, "shelly_on": not cur if ok else cur}
