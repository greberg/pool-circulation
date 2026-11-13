"""Binary sensor platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PoolCirculationCoordinator
from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolCirculationCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolPumpFaultBinarySensor(coordinator, entry)])


class PoolPumpFaultBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """On when the pump does not follow the controller outputs.

    Needs a pump feedback sensor (power or rpm). ``not_running``: the pump
    should run but the feedback reads nothing. ``not_stopping``: it should be
    off but still draws power. ``wrong_speed``: it runs, but at another speed
    than selected after the start-up ramp. Without a feedback sensor this
    stays off.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: PoolCirculationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Pool Pump Fault"
        self._attr_unique_id = f"{entry.entry_id}_pump_fault"
        self._attr_icon = "mdi:pump-off"
        self._attr_device_info = device_info(entry)

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}

    @property
    def is_on(self) -> bool:
        return self._data.get("pump_fault") is not None

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data
        return {
            "fault": d.get("pump_fault"),
            "since": d.get("pump_fault_since"),
            "mode": d.get("mode"),
            "actual_speed": d.get("actual_speed"),
            "feedback": d.get("feedback_value"),
            "feedback_unit": d.get("feedback_unit"),
        }
