"""Settings routes (package 2): /api/settings. Contract: docs/api.md "Settings".

The response adds the read-only ``about`` block (display paths, data dir, version) from
``services.config``. ``default_key_fp`` must name a known key (422 ``key_unknown``).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from portkey.api.deps import ServicesDep, require_auth
from portkey.api.routes_connections import require_known_key
from portkey.api.schemas import About, SettingsOut, SettingsPatch
from portkey.models import AppSettings
from portkey.services import Services

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


def _out(services: Services, settings: AppSettings) -> SettingsOut:
    config = services.config
    about = About(
        ssh_dir=config.ssh_dir_display,
        keys_dir=config.keys_dir_display,
        data_dir=str(config.data_dir),
        version=config.version,
    )
    return SettingsOut(**asdict(settings), about=about)


@router.get("", response_model=SettingsOut)
def get_settings(services: ServicesDep) -> SettingsOut:
    return _out(services, services.settings.get())


@router.patch("", response_model=SettingsOut)
def update_settings(body: SettingsPatch, services: ServicesDep) -> SettingsOut:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("default_key_fp") is not None:
        require_known_key(services, changes["default_key_fp"])
    return _out(services, services.settings.update(changes))
