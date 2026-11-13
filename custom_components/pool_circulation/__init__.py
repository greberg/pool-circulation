"""Pool Circulation integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    ALL_ENTITIES,
    CARD_URL,
    CONF_SENSOR_PUMP_FEEDBACK,
    DOMAIN,
    ENTITY_SUFFIXES,
    VERSION,
)
from .coordinator import PoolCirculationCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace cards and load them on every dashboard."""
    if hass.data.get(f"{DOMAIN}_card_registered") or hass.http is None:
        return
    hass.data[f"{DOMAIN}_card_registered"] = True
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    path = str(Path(__file__).parent / "www" / "pool-circulation-card.js")
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, path, False)])
    # Version in the URL so browsers pick up a new card after an update
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop entities that older versions created but this one no longer does."""
    reg = er.async_get(hass)
    current = {
        f"{entry.entry_id}_{suffix}" for suffixes in ENTITY_SUFFIXES.values() for suffix in suffixes
    }
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id not in current:
            _LOGGER.info("Removing entity from an older version: %s", ent.entity_id)
            reg.async_remove(ent.entity_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 → v2: the old "actual RPM sensor" becomes the pump feedback sensor."""
    if entry.version == 1:
        def move(cfg: dict) -> dict:
            cfg = dict(cfg)
            old = cfg.pop("sensor_actual_rpm", None)
            if old and not cfg.get(CONF_SENSOR_PUMP_FEEDBACK):
                cfg[CONF_SENSOR_PUMP_FEEDBACK] = old
            return cfg

        hass.config_entries.async_update_entry(
            entry, data=move(entry.data), options=move(entry.options), version=2
        )
        _LOGGER.info("Migrated Pool Circulation config to version 2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await _async_register_card(hass)
    _remove_stale_entities(hass, entry)

    coordinator = PoolCirculationCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_unload()

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when an entity changed; otherwise just re-decide with the new settings.

    Re-deciding in place keeps sensors live and today's runtime untouched.
    """
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    prev = coordinator.cfg_snapshot
    curr = coordinator.cfg
    coordinator.cfg_snapshot = curr

    if any((prev.get(k) or "") != (curr.get(k) or "") for k in ALL_ENTITIES):
        _LOGGER.debug("Pool Circulation: entities changed — reloading")
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        await coordinator.async_evaluate_mode()
