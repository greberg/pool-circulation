"""Switch platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PoolCirculationCoordinator

_DEVICE_INFO = lambda entry: {
    "identifiers": {(DOMAIN, entry.entry_id)},
    "name": entry.title,
    "manufacturer": "Pool Circulation",
    "model": "Price-based Scheduler",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PoolCirculationAutomationSwitch(coordinator, entry),
            PoolExtraFilterSwitch(coordinator, entry),
            PoolWinterModeSwitch(coordinator, entry),
        ]
    )


class PoolCirculationAutomationSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable / disable price-based automation."""

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Circulation Automation"
        self._attr_unique_id = f"{entry.entry_id}_automation"
        self._attr_icon = "mdi:water-pump"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.automation_enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_automation(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_automation(False)
        self.async_write_ha_state()


class PoolExtraFilterSwitch(CoordinatorEntity, SwitchEntity):
    """Activate extra filter mode — forces high RPM for a configurable duration.

    Useful after bathing to run extra filtration. Overrides price logic and
    runs faster than any freeze guard. Auto-disables after the configured
    duration (default 60 min) — also across HA restarts.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Extra Filter"
        self._attr_unique_id = f"{entry.entry_id}_extra_filter"
        self._attr_icon = "mdi:filter-plus"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.extra_filter_active

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "duration_minutes": self.coordinator.extra_filter_duration,
            "remaining_seconds": (self.coordinator.data or {}).get("extra_filter_remaining", 0),
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_extra_filter(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_extra_filter(False)
        self.async_write_ha_state()


class PoolWinterModeSwitch(CoordinatorEntity, SwitchEntity):
    """Winter / summer season toggle.

    On (winter): heat pump is not touched at all (it is usually offline for
    the season — turn it off yourself). Circulation follows the winter rules:
    frost and pool-temperature guards first, then price-optimised low-speed
    running depending on outdoor and pool temperature.
    Off (summer): daily filtration hours + heat pump managed by price.
    """

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = "Pool Winter Mode"
        self._attr_unique_id = f"{entry.entry_id}_winter_mode"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def icon(self) -> str:
        return "mdi:snowflake" if self.coordinator.winter_mode else "mdi:white-balance-sunny"

    @property
    def is_on(self) -> bool:
        return self.coordinator.winter_mode

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, winter: bool) -> None:
        await self.coordinator.async_set_winter_mode(winter)
        self.async_write_ha_state()
