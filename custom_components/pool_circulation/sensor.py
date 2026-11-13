"""Sensor platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import PoolCirculationCoordinator


def device_info(entry: ConfigEntry) -> dict:
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "Pool Circulation",
        "model": "Season & price scheduler",
        "sw_version": VERSION,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            PoolCirculationModeSensor(coordinator, entry),
            PoolCirculationReasonSensor(coordinator, entry),
            PoolCirculationHoursTodaySensor(coordinator, entry),
            PoolCirculationHoursRemainingSensor(coordinator, entry),
        ]
    )


class _SensorBase(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, key, name, icon):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_device_info = device_info(entry)

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}


class PoolCirculationModeSensor(_SensorBase):
    """Current circulation mode: off / low / medium / high.

    Attributes carry everything the dashboard cards need. The bulky ones are
    kept out of the recorder.
    """

    _unrecorded_attributes = frozenset({"plan", "entities", "thresholds"})

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "mode", "Pool Circulation Mode", "mdi:pump")

    @property
    def native_value(self):
        return self._data.get("mode", "off")

    @property
    def extra_state_attributes(self):
        d = self._data
        return {
            key: d.get(key)
            for key in (
                "season", "rule", "reason", "safety", "winter_tier",
                "automation_enabled", "pump_running", "extra_filter_active",
                "extra_filter_remaining", "cover_circulation_active", "uv_on",
                "pool_temp", "outdoor_temp", "price", "is_peak_price",
                "target_hours", "done_hours", "must_run",
                "in_cooldown", "in_min_on", "unavailable_entities",
                "thresholds", "plan", "entities",
            )
        }


class PoolCirculationReasonSensor(_SensorBase):
    """Plain-language explanation of why the pump is in its current mode."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "reason", "Pool Circulation Reason", "mdi:information-outline")

    @property
    def native_value(self):
        return (self._data.get("reason") or "")[:255]


class PoolCirculationHoursTodaySensor(_SensorBase):
    """Hours the pump has run today (all speeds)."""

    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hours_today", "Pool Circulation Hours Today", "mdi:clock-check")

    @property
    def native_value(self):
        return self._data.get("hours_total_today", 0)

    @property
    def extra_state_attributes(self):
        return {
            "medium_high_hours": self._data.get("hours_run_today"),
            "low_hours": self._data.get("hours_low_today"),
        }


class PoolCirculationHoursRemainingSensor(_SensorBase):
    """Hours still needed to meet today's target for the current season."""

    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hours_remaining", "Pool Circulation Hours Remaining", "mdi:clock-outline")

    @property
    def native_value(self):
        return self._data.get("hours_remaining", 0)
