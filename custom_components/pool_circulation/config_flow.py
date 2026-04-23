"""Config flow for Pool Circulation integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BINARY_BEST_PRICE,
    CONF_BINARY_PEAK_PRICE,
    CONF_CLIMATE_HEAT_PUMP,
    CONF_DAILY_HOURS,
    CONF_SENSOR_OUTDOOR_TEMP,
    CONF_SENSOR_POOL_TEMP,
    CONF_SENSOR_PRICE,
    CONF_SENSOR_PRICE_LEVEL,
    CONF_SWITCH_CIRCULATION,
    CONF_SWITCH_RPM_HIGH,
    CONF_SWITCH_RPM_LOW,
    CONF_SWITCH_RPM_MEDIUM,
    CONF_TEMP_ALGAE_THRESHOLD,
    CONF_TEMP_FREEZE_THRESHOLD,
    DEFAULT_BINARY_BEST_PRICE,
    DEFAULT_BINARY_PEAK_PRICE,
    DEFAULT_DAILY_HOURS,
    DEFAULT_NAME,
    DEFAULT_SENSOR_PRICE,
    DEFAULT_SENSOR_PRICE_LEVEL,
    DEFAULT_TEMP_ALGAE_THRESHOLD,
    DEFAULT_TEMP_FREEZE_THRESHOLD,
    DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default=DEFAULT_NAME): str,
        # Physical devices
        vol.Required(CONF_CLIMATE_HEAT_PUMP): str,
        vol.Required(CONF_SWITCH_CIRCULATION): str,
        vol.Optional(CONF_SWITCH_RPM_LOW, default=""): str,
        vol.Optional(CONF_SWITCH_RPM_MEDIUM, default=""): str,
        vol.Optional(CONF_SWITCH_RPM_HIGH, default=""): str,
        # Price signals
        vol.Optional(CONF_SENSOR_PRICE, default=DEFAULT_SENSOR_PRICE): str,
        vol.Optional(CONF_SENSOR_PRICE_LEVEL, default=DEFAULT_SENSOR_PRICE_LEVEL): str,
        vol.Optional(CONF_BINARY_BEST_PRICE, default=DEFAULT_BINARY_BEST_PRICE): str,
        vol.Optional(CONF_BINARY_PEAK_PRICE, default=DEFAULT_BINARY_PEAK_PRICE): str,
        # Schedule
        vol.Required(CONF_DAILY_HOURS, default=DEFAULT_DAILY_HOURS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=24)
        ),
        # Temperature sensors for cold-weather override
        vol.Optional(CONF_SENSOR_OUTDOOR_TEMP, default=""): str,
        vol.Optional(CONF_SENSOR_POOL_TEMP, default=""): str,
        vol.Optional(
            CONF_TEMP_ALGAE_THRESHOLD, default=DEFAULT_TEMP_ALGAE_THRESHOLD
        ): vol.Coerce(float),
        vol.Optional(
            CONF_TEMP_FREEZE_THRESHOLD, default=DEFAULT_TEMP_FREEZE_THRESHOLD
        ): vol.Coerce(float),
    }
)


class PoolCirculationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Circulation."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get("name", DEFAULT_NAME),
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return PoolCirculationOptionsFlow(config_entry)


class PoolCirculationOptionsFlow(config_entries.OptionsFlow):
    """Pool Circulation options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        cfg = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DAILY_HOURS,
                    default=cfg.get(CONF_DAILY_HOURS, DEFAULT_DAILY_HOURS),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=24)),
                vol.Optional(
                    CONF_BINARY_BEST_PRICE,
                    default=cfg.get(CONF_BINARY_BEST_PRICE, DEFAULT_BINARY_BEST_PRICE),
                ): str,
                vol.Optional(
                    CONF_BINARY_PEAK_PRICE,
                    default=cfg.get(CONF_BINARY_PEAK_PRICE, DEFAULT_BINARY_PEAK_PRICE),
                ): str,
                vol.Optional(
                    CONF_SENSOR_PRICE,
                    default=cfg.get(CONF_SENSOR_PRICE, DEFAULT_SENSOR_PRICE),
                ): str,
                vol.Optional(
                    CONF_SENSOR_OUTDOOR_TEMP,
                    default=cfg.get(CONF_SENSOR_OUTDOOR_TEMP, ""),
                ): str,
                vol.Optional(
                    CONF_SENSOR_POOL_TEMP,
                    default=cfg.get(CONF_SENSOR_POOL_TEMP, ""),
                ): str,
                vol.Optional(
                    CONF_TEMP_ALGAE_THRESHOLD,
                    default=cfg.get(CONF_TEMP_ALGAE_THRESHOLD, DEFAULT_TEMP_ALGAE_THRESHOLD),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_TEMP_FREEZE_THRESHOLD,
                    default=cfg.get(CONF_TEMP_FREEZE_THRESHOLD, DEFAULT_TEMP_FREEZE_THRESHOLD),
                ): vol.Coerce(float),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
