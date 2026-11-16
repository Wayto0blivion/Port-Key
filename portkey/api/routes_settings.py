"""Settings routes (package 2): /api/settings. Contract: docs/api.md "Settings".

The response adds the read-only ``about`` block (display paths, data dir, version) from
``services.config``. ``default_key_fp`` must name a known key (422 ``key_unknown``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.schemas import SettingsOut, SettingsPatch

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("", response_model=SettingsOut)
def get_settings(services: ServicesDep) -> SettingsOut:
    raise NotImplementedError("package 2")


@router.patch("", response_model=SettingsOut)
def update_settings(body: SettingsPatch, services: ServicesDep) -> SettingsOut:
    raise NotImplementedError("package 2")
