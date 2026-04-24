"""Sensor platform for Pool Circulation."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
            PoolCirculationModeSensor(coordinator, entry),
            PoolCirculationRpmSensor(coordinator, entry),
            PoolCirculationHoursTodaySensor(coordinator, entry),
            PoolCirculationHoursRemainingSensor(coordinator, entry),
            PoolCirculationPriceSensor(coordinator, entry),
            PoolCirculationPriceLevelSensor(coordinator, entry),
            PoolOutdoorTempSensor(coordinator, entry),
            PoolPoolTempSensor(coordinator, entry),
            PoolHeatPumpModeSensor(coordinator, entry),
            PoolHeatPumpCurrentTempSensor(coordinator, entry),
        ]
    )


class _SensorBase(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, key, name):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _DEVICE_INFO(entry)

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}


class PoolCirculationModeSensor(_SensorBase):
    """Current circulation mode: off / low / medium / high."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "mode", "Pool Circulation Mode")
        self._attr_icon = "mdi:pump"

    @property
    def native_value(self):
        return self._data.get("mode", "off")

    @property
    def extra_state_attributes(self):
        d = self._data
        return {
            "is_best_price": d.get("is_best_price"),
            "is_peak_price": d.get("is_peak_price"),
            "must_run": d.get("must_run"),
            "too_cold": d.get("too_cold"),
            "freeze_risk": d.get("freeze_risk"),
            "extra_filter_active": d.get("extra_filter_active"),
            "uv_on": d.get("uv_on"),
            "outdoor_temp": d.get("outdoor_temp"),
            "pool_temp": d.get("pool_temp"),
            "price": d.get("price"),
            "price_level": d.get("price_level"),
        }


class PoolCirculationHoursTodaySensor(_SensorBase):
    """Hours the circulation pump has run today."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hours_today", "Pool Circulation Hours Today")
        self._attr_native_unit_of_measurement = "h"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:clock-check"

    @property
    def native_value(self):
        return self._data.get("hours_run_today", 0)


class PoolCirculationHoursRemainingSensor(_SensorBase):
    """Hours still needed to meet today's target."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hours_remaining", "Pool Circulation Hours Remaining")
        self._attr_native_unit_of_measurement = "h"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:clock-outline"

    @property
    def native_value(self):
        return self._data.get("hours_remaining", 0)


class PoolCirculationPriceSensor(_SensorBase):
    """Current electricity price."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "price", "Pool Electricity Price")
        self._attr_native_unit_of_measurement = "SEK/kWh"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:lightning-bolt"

    @property
    def native_value(self):
        val = self._data.get("price")
        return round(val, 3) if val is not None else None


class PoolCirculationPriceLevelSensor(_SensorBase):
    """Current electricity price level label."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "price_level", "Pool Electricity Price Level")
        self._attr_icon = "mdi:cash"

    @property
    def native_value(self):
        return self._data.get("price_level")


class PoolOutdoorTempSensor(_SensorBase):
    """Outdoor temperature passthrough — hidden by default if no sensor configured."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "outdoor_temp", "Pool Outdoor Temperature")
        self._attr_native_unit_of_measurement = "°C"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self._data.get("outdoor_temp")


class PoolPoolTempSensor(_SensorBase):
    """Pool water temperature passthrough — hidden by default if no sensor configured."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "pool_temp", "Pool Water Temperature")
        self._attr_native_unit_of_measurement = "°C"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:pool-thermometer"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self._data.get("pool_temp")


class PoolCirculationRpmSensor(_SensorBase):
    """Current circulation pump RPM — reads actual switch states."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "rpm", "Pool Circulation RPM")
        self._attr_native_unit_of_measurement = "RPM"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:speedometer"

    @property
    def native_value(self):
        # 0 when pump is off
        return self._data.get("active_rpm", 0)


class PoolHeatPumpModeSensor(_SensorBase):
    """Current HVAC mode of the heat pump (off / cool / heat / auto)."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hp_mode", "Pool Heat Pump Mode")
        self._attr_icon = "mdi:heat-pump"

    @property
    def native_value(self):
        return self._data.get("hp_mode")


class PoolHeatPumpCurrentTempSensor(_SensorBase):
    """Current temperature reading from the heat pump."""

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hp_current_temp", "Pool Heat Pump Current Temperature")
        self._attr_native_unit_of_measurement = "°C"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self):
        val = self._data.get("hp_current_temp")
        return round(float(val), 1) if val is not None else None

    @property
    def extra_state_attributes(self):
        return {
            "target_temperature": self._data.get("hp_target_temp"),
            "fan_mode": self._data.get("hp_fan_mode"),
            "hvac_mode": self._data.get("hp_mode"),
        }
