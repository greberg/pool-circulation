"""Coordinator smoke tests against a minimal stubbed Home Assistant.

Exercises the real coordinator code paths that matter for restarts:
reconciliation after a pump-controller reboot, persisted counters and
timers across an HA restart, winter heat-pump hands-off and safety guards.

Run with:  python3 -m unittest discover -s tests
"""
import asyncio
import importlib
import sys
import types
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=1))


# ---------------------------------------------------------------------------
# Home Assistant stubs
# ---------------------------------------------------------------------------
class Clock:
    now = datetime(2026, 11, 13, 11, 30, tzinfo=TZ)


class State:
    def __init__(self, entity_id, state, attributes=None):
        self.entity_id, self.state, self.attributes = entity_id, state, attributes or {}


class States(dict):
    def set(self, entity_id, state, attributes=None):
        old = self.get(entity_id)
        new = State(entity_id, state, attributes if attributes is not None else (old.attributes if old else {}))
        self[entity_id] = new
        return old, new


class FakeHass:
    def __init__(self):
        self.states = States()
        self.calls = []
        self.events = []
        self.listeners = []          # (entity_ids, callback)
        self.tasks = []
        self.is_running = True
        self.http = None
        hass = self

        class Services:
            async def async_call(self, domain, service, data, blocking=False):
                hass.calls.append((domain, service, dict(data)))
                eid = data["entity_id"]
                if service in ("turn_on", "turn_off") and domain != "climate":
                    hass.set_state(eid, "on" if service == "turn_on" else "off")
                elif service == "set_temperature":
                    st = hass.states[eid]
                    hass.set_state(eid, st.state, {**st.attributes, "temperature": data["temperature"]})

        class Bus:
            def async_fire(self, event, data):
                hass.events.append((event, data))

            def async_listen_once(self, event, cb):
                return lambda: None

        self.services, self.bus = Services(), Bus()

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def set_state(self, entity_id, state, attributes=None):
        old, new = self.states.set(entity_id, state, attributes)
        event = types.SimpleNamespace(data={"old_state": old, "new_state": new})
        for ids, cb in list(self.listeners):
            if entity_id in ids:
                cb(event)

    async def settle(self):
        for _ in range(10):
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending)


_STORE = {}


def _install_stubs():
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    mod("homeassistant")
    mod("homeassistant.const", EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        Platform=types.SimpleNamespace(BINARY_SENSOR="binary_sensor", SENSOR="sensor",
                                       SWITCH="switch", NUMBER="number"))
    mod("homeassistant.core", HomeAssistant=FakeHass, Event=object, callback=lambda f: f)
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod("homeassistant.helpers")

    class Registry:
        def async_get_entity_id(self, domain, platform, unique_id):
            return f"{domain}.{unique_id}"

    mod("homeassistant.helpers.entity_registry", async_get=lambda hass: Registry())

    def track_state(hass, ids, cb):
        entry = (list(ids), cb)
        hass.listeners.append(entry)
        return lambda: hass.listeners.remove(entry)

    def track_time(hass, cb, **kw):
        hass.tick = cb
        return lambda: None

    mod("homeassistant.helpers.event",
        async_track_state_change_event=track_state, async_track_time_change=track_time)

    class Store:
        def __init__(self, hass, version, key):
            self.key = key

        async def async_load(self):
            return _STORE.get(self.key)

        async def async_save(self, data):
            _STORE[self.key] = data

    mod("homeassistant.helpers.storage", Store=Store)

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name, update_interval):
            self.hass, self.data = hass, None

        def async_set_updated_data(self, data):
            self.data = data

    mod("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator)
    mod("homeassistant.util")
    dt = mod(
        "homeassistant.util.dt",
        now=lambda: Clock.now,
        as_local=lambda d: d.astimezone(TZ),
        start_of_local_day=lambda d: d.replace(hour=0, minute=0, second=0, microsecond=0),
        DEFAULT_TIME_ZONE=TZ,
    )
    sys.modules["homeassistant.util"].dt = dt

    # Just enough of voluptuous / selectors / flows to import config_flow
    class Marker(str):
        def __new__(cls, key, default=None, description=None):
            m = str.__new__(cls, key)
            m.default, m.description = default, description
            return m

    mod("voluptuous", Schema=lambda d: d, Required=Marker, Optional=Marker)
    sys.modules["homeassistant.config_entries"].ConfigFlow = type(
        "ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
    sys.modules["homeassistant.config_entries"].OptionsFlow = object
    sys.modules["homeassistant.config_entries"].ConfigEntry = object
    mod("homeassistant.data_entry_flow", FlowResult=dict, section=lambda schema, opts: ("section", schema, opts))
    selector = mod("homeassistant.helpers.selector")
    for name in ("EntitySelector", "EntitySelectorConfig", "NumberSelector",
                 "NumberSelectorConfig", "TextSelector"):
        setattr(selector, name, lambda *a, **k: (a, k))
    selector.NumberSelectorMode = types.SimpleNamespace(BOX="box")


_install_stubs()
_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "pool_circulation"
_pkg = types.ModuleType("pcc")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["pcc"] = _pkg
coord_mod = importlib.import_module("pcc.coordinator")
flow_mod = importlib.import_module("pcc.config_flow")
init_mod = importlib.import_module("pcc.__init__")

PRICES = [0.10, 0.08, 0.05, 0.06, 0.09, 0.20, 0.50, 1.20, 1.50, 1.10, 0.70, 0.60,
          0.55, 0.50, 0.45, 0.60, 0.90, 1.60, 1.80, 1.70, 1.00, 0.40, 0.30, 0.20]

CFG = {
    "climate_heat_pump": "climate.hp",
    "switch_circulation": "switch.pump",
    "switch_rpm_low": "switch.low",
    "switch_rpm_medium": "switch.medium",
    "switch_rpm_high": "switch.high",
    "switch_uv_lamp": "switch.uv",
    "sensor_price": "sensor.price",
    "sensor_outdoor_temp": "sensor.outdoor",
    "sensor_pool_temp": "sensor.pool",
    "cooldown_minutes": 0,
    "min_on_minutes": 0,
}


def make_hass(outdoor="3", pool="8"):
    hass = FakeHass()
    for e in ("switch.pump", "switch.low", "switch.medium", "switch.high", "switch.uv"):
        hass.states.set(e, "off")
    hass.states.set("climate.hp", "heat", {"temperature": 30})
    hass.states.set("sensor.price", "0.6", {"today": PRICES})
    hass.states.set("sensor.outdoor", outdoor)
    hass.states.set("sensor.pool", pool)
    return hass


async def start(hass, store_winter=None, extra=None):
    entry = types.SimpleNamespace(entry_id="e1", data={**CFG, **(extra or {})}, options={})
    c = coord_mod.PoolCirculationCoordinator(hass, entry)
    if store_winter is not None:
        _STORE.setdefault("pool_circulation_e1", {})["winter_mode"] = store_winter
    await c.async_setup()
    await hass.settle()
    return c


def on(hass, e):
    return hass.states[e].state == "on"


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        _STORE.clear()
        Clock.now = datetime(2026, 11, 13, 11, 30, tzinfo=TZ)

    def run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_winter_warm_pool_runs_low_and_leaves_heat_pump_alone(self):
        async def go():
            hass = make_hass(outdoor="3", pool="8")
            c = await start(hass, store_winter=True)
            self.assertEqual(c.current_mode, "low")
            self.assertTrue(on(hass, "switch.pump") and on(hass, "switch.low"))
            self.assertFalse(on(hass, "switch.medium"))
            self.assertFalse([x for x in hass.calls if x[0] == "climate"])
            self.assertEqual(c.data["winter_tier"], "algae")
            self.assertIsNone(c.data["target_hours"])
        self.run_async(go())

    def test_controller_reboot_is_reconciled_immediately(self):
        async def go():
            hass = make_hass()
            c = await start(hass, store_winter=True)
            hass.set_state("switch.pump", "unavailable")
            hass.set_state("switch.low", "unavailable")
            await hass.settle()
            # Controller comes back with everything off
            hass.set_state("switch.pump", "off")
            hass.set_state("switch.low", "off")
            await hass.settle()
            self.assertTrue(on(hass, "switch.pump") and on(hass, "switch.low"))
            self.assertEqual(c.current_mode, "low")
        self.run_async(go())

    def test_minute_tick_reconciles_manual_drift(self):
        async def go():
            hass = make_hass()
            await start(hass, store_winter=True)
            hass.set_state("switch.low", "off")   # someone switched it off
            await hass.tick(Clock.now + timedelta(minutes=1))
            await hass.settle()
            self.assertTrue(on(hass, "switch.low"))
        self.run_async(go())

    def test_runtime_and_extra_filter_survive_restart(self):
        async def go():
            hass = make_hass(outdoor="12", pool="24")
            c = await start(hass, store_winter=False)
            await c.async_set_extra_filter(True)
            await hass.settle()
            self.assertEqual(c.current_mode, "high")
            for _ in range(30):
                Clock.now += timedelta(minutes=1)
                await hass.tick(Clock.now)
            await hass.settle()
            self.assertAlmostEqual(c._minutes_run, 30, delta=0.01)
            await c.async_unload()

            # HA down for 10 minutes — not counted
            Clock.now += timedelta(minutes=10)
            hass2 = make_hass(outdoor="12", pool="24")
            for e in ("switch.pump", "switch.high"):
                hass2.states.set(e, "off")      # controller lost state too
            c2 = await start(hass2)
            self.assertAlmostEqual(c2._minutes_run, 30, delta=0.01)
            self.assertTrue(c2.extra_filter_active)
            self.assertTrue(on(hass2, "switch.pump") and on(hass2, "switch.high"))
            # Extra filter (60 min) ends 20 min later
            for _ in range(21):
                Clock.now += timedelta(minutes=1)
                await hass2.tick(Clock.now)
            await hass2.settle()
            self.assertFalse(c2.extra_filter_active)
            self.assertNotEqual(c2.current_mode, "high")
        self.run_async(go())

    def test_day_rollover_resets_counters(self):
        async def go():
            Clock.now = datetime(2026, 11, 13, 23, 58, tzinfo=TZ)
            hass = make_hass(outdoor="12", pool="24")
            c = await start(hass, store_winter=False)
            c._minutes_run = 400
            for _ in range(4):
                Clock.now += timedelta(minutes=1)
                await hass.tick(Clock.now)
            await hass.settle()
            self.assertLess(c._minutes_run + c._minutes_low, 3)
            self.assertEqual(c._day.isoformat(), "2026-11-14")
        self.run_async(go())

    def test_pool_critical_overrides_manual(self):
        async def go():
            hass = make_hass(outdoor="-2", pool="6")
            c = await start(hass, store_winter=True)
            await c.async_set_automation(False)
            hass.set_state("sensor.pool", "3.6")
            await hass.settle()
            self.assertEqual(c.current_mode, "medium")
            self.assertTrue(on(hass, "switch.medium"))
            self.assertEqual(c.data["safety"], "pool_critical")
        self.run_async(go())

    def test_winter_pool_between_limits_stops_pump(self):
        async def go():
            hass = make_hass(outdoor="5", pool="5.5")
            c = await start(hass, store_winter=True)
            self.assertEqual(c.current_mode, "off")
            self.assertFalse(on(hass, "switch.pump") or on(hass, "switch.low"))
            hass.set_state("sensor.outdoor", "1")          # frost guard
            await hass.settle()
            self.assertEqual(c.current_mode, "low")
            hass.set_state("sensor.pool", "3.8")           # critical, outdoor ≥ 0
            await hass.settle()
            self.assertEqual(c.current_mode, "low")
            hass.set_state("sensor.outdoor", "-1")
            await hass.settle()
            self.assertEqual(c.current_mode, "medium")
        self.run_async(go())

    def test_summer_heat_pump_only_commanded_on_change(self):
        async def go():
            hass = make_hass(outdoor="15", pool="26")
            hass.states.set("climate.hp", "heat", {"temperature": 25})
            await start(hass, store_winter=False)
            first = [x for x in hass.calls if x[0] == "climate"]
            self.assertEqual(first, [("climate", "set_temperature", {"entity_id": "climate.hp", "temperature": 30.0})])
            hass.calls.clear()
            for _ in range(3):
                Clock.now += timedelta(minutes=1)
                await hass.tick(Clock.now)
            await hass.settle()
            self.assertEqual([x for x in hass.calls if x[0] == "climate"], [])
        self.run_async(go())

    def test_unavailable_heat_pump_never_breaks_circulation(self):
        async def go():
            hass = make_hass(outdoor="15", pool="26")

            real = hass.services.async_call

            async def call(domain, service, data, blocking=False):
                if domain == "climate":
                    raise RuntimeError("设备响应超时")
                await real(domain, service, data, blocking)

            hass.services.async_call = call
            hass.states.set("climate.hp", "off", {"temperature": 20})
            c = await start(hass, store_winter=False)
            self.assertTrue(on(hass, "switch.pump"))
            self.assertIn("climate.hp", c.data["unavailable_entities"])
        self.run_async(go())


class FeedbackTests(unittest.TestCase):
    """The ESP switches only show what the speed controller was told —
    a power/rpm sensor shows what the pump really does."""

    FB = {"sensor_pump_feedback": "sensor.power"}

    def setUp(self):
        _STORE.clear()
        Clock.now = datetime(2026, 11, 13, 11, 30, tzinfo=TZ)

    def run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    async def tick(self, hass, minutes):
        for _ in range(minutes):
            Clock.now += timedelta(minutes=1)
            await hass.tick(Clock.now)
        await hass.settle()

    def test_fault_when_pump_does_not_follow_and_outputs_resent(self):
        async def go():
            hass = make_hass()
            hass.states.set("sensor.power", "0", {"unit_of_measurement": "W"})
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                c = await start(hass, store_winter=True, extra=self.FB)
                self.assertTrue(on(hass, "switch.pump"))
                await self.tick(hass, 2)
                self.assertIsNone(c.pump_fault)           # still in grace
                hass.calls.clear()
                await self.tick(hass, 2)
            self.assertEqual(c.pump_fault, "not_running")
            faults = [d for e, d in hass.events if e == "pool_circulation_pump_fault"]
            self.assertEqual((faults[-1]["fault"], faults[-1]["active"]), ("not_running", True))
            # Enable pulsed off → on, speed re-sent
            self.assertEqual(
                [(x[1], x[2]["entity_id"]) for x in hass.calls if x[0] == "switch"],
                [("turn_off", "switch.pump"), ("turn_on", "switch.low"), ("turn_on", "switch.pump")],
            )
            hass.set_state("sensor.power", "180")
            await self.tick(hass, 1)
            self.assertIsNone(c.pump_fault)
            self.assertFalse(c.data["pump_fault"])
        self.run_async(go())

    def test_runtime_counts_feedback_not_switches(self):
        async def go():
            hass = make_hass()
            hass.states.set("sensor.power", "0", {"unit_of_measurement": "W"})
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                c = await start(hass, store_winter=True, extra=self.FB)
                await self.tick(hass, 10)
                self.assertEqual(c._minutes_low, 0)
                hass.set_state("sensor.power", "200")
                await self.tick(hass, 10)
            self.assertAlmostEqual(c._minutes_low, 10, delta=0.01)
        self.run_async(go())

    def test_unavailable_feedback_falls_back_to_switches(self):
        async def go():
            hass = make_hass()
            hass.states.set("sensor.power", "unavailable")
            c = await start(hass, store_winter=True, extra=self.FB)
            await self.tick(hass, 5)
            self.assertIsNone(c.pump_fault)
            self.assertAlmostEqual(c._minutes_low, 5, delta=0.01)
        self.run_async(go())

    def test_startup_ramp_is_not_a_fault_but_a_wrong_speed_is(self):
        async def go():
            hass = make_hass()
            # Controller starts at full speed although LOW is selected
            hass.states.set("sensor.power", "620", {"unit_of_measurement": "W"})
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                c = await start(hass, store_winter=True, extra=self.FB)
                self.assertEqual(c.current_mode, "low")
                self.assertEqual(c.data["actual_speed"], "high")
                self.assertTrue(c.data["speed_settling"])
                await self.tick(hass, 8)              # 6 min settle + < 3 min grace
                self.assertIsNone(c.pump_fault)
                self.assertFalse(c.data["speed_settling"])
                await self.tick(hass, 2)
                self.assertEqual(c.pump_fault, "wrong_speed")
                hass.set_state("sensor.power", "180")
                self.assertEqual(c.data["actual_speed"], "low")   # refreshed without a tick
                await self.tick(hass, 1)
            self.assertIsNone(c.pump_fault)
        self.run_async(go())

    def test_speed_settles_within_ramp_time(self):
        async def go():
            hass = make_hass()
            hass.states.set("sensor.power", "620", {"unit_of_measurement": "W"})
            c = await start(hass, store_winter=True, extra=self.FB)
            await self.tick(hass, 4)
            hass.set_state("sensor.power", "190")
            await self.tick(hass, 10)
            self.assertIsNone(c.pump_fault)
            self.assertEqual(c.data["actual_speed"], "low")
        self.run_async(go())

    def test_rpm_feedback_and_custom_bands(self):
        async def go():
            hass = make_hass()
            hass.states.set("sensor.power", "2900", {"unit_of_measurement": "rpm"})
            c = await start(hass, store_winter=True, extra=self.FB)
            self.assertEqual(c._actual_speed(), "high")
            hass.set_state("sensor.power", "2400")
            self.assertEqual(c._actual_speed(), "medium")
            hass.set_state("sensor.power", "1600")
            self.assertEqual(c._actual_speed(), "low")
            hass.set_state("sensor.power", "0")
            self.assertEqual(c._actual_speed(), "off")

            hass2 = make_hass()
            hass2.states.set("sensor.power", "250", {"unit_of_measurement": "kW?"})
            c2 = await start(hass2, store_winter=True, extra={
                **self.FB, "feedback_medium_from": 200, "feedback_high_from": 400})
            self.assertEqual(c2._actual_speed(), "medium")

            hass3 = make_hass()
            hass3.states.set("sensor.power", "250", {"unit_of_measurement": "A"})
            c3 = await start(hass3, store_winter=True, extra=self.FB)
            self.assertIsNone(c3._actual_speed())      # unknown unit, no bands
            self.assertTrue(c3._pump_running())
        self.run_async(go())

    def test_energy_and_cost_today_from_power_feedback(self):
        async def go():
            hass = make_hass()                       # price sensor reads 0.6
            hass.states.set("sensor.power", "180", {"unit_of_measurement": "W"})
            c = await start(hass, store_winter=True, extra=self.FB)
            await self.tick(hass, 60)                # 180 W for one hour
            self.assertAlmostEqual(c.data["energy_today"], 0.18, places=3)
            self.assertAlmostEqual(c.data["cost_today"], 0.11, places=2)   # 0.18 kWh × 0.6
            await c._save_state()
            await c.async_unload()

            hass2 = make_hass()
            hass2.states.set("sensor.power", "180", {"unit_of_measurement": "W"})
            c2 = await start(hass2, extra=self.FB)
            self.assertAlmostEqual(c2.data["energy_today"], 0.18, places=3)

            no_power = make_hass()
            no_power.states.set("sensor.power", "1600", {"unit_of_measurement": "rpm"})
            c3 = await start(no_power, store_winter=True, extra=self.FB)
            await self.tick(no_power, 5)
            self.assertIsNone(c3.data["energy_today"])
            self.assertEqual(c3.data["price"], 0.6)
        self.run_async(go())

    def test_speed_selected_before_controller_enabled(self):
        async def go():
            hass = make_hass()
            await start(hass, store_winter=True)
            order = [x[2]["entity_id"] for x in hass.calls if x[1] == "turn_on"]
            self.assertLess(order.index("switch.low"), order.index("switch.pump"))
        self.run_async(go())

    def test_actual_rpm_setting_migrates_to_feedback(self):
        async def go():
            hass = make_hass()
            updates = {}
            hass.config_entries = types.SimpleNamespace(
                async_update_entry=lambda entry, **kw: updates.update(kw))
            entry = types.SimpleNamespace(version=1, data={"sensor_actual_rpm": "sensor.cirkulation_rpm"},
                                          options={})
            self.assertTrue(await init_mod.async_migrate_entry(hass, entry))
            self.assertEqual(updates["data"], {"sensor_pump_feedback": "sensor.cirkulation_rpm"})
            self.assertEqual(updates["version"], 2)
        self.run_async(go())


class ConfigFlowTests(unittest.TestCase):
    def test_flatten_lifts_sections_and_blanks_cleared_devices(self):
        data = flow_mod._flatten({
            "switch_rpm_low": "switch.low", "daily_hours": 8.0,
            "optional_devices": {"climate_heat_pump": "climate.hp"},
            "advanced": {"temp_outdoor_medium": -1.0, "pool_critical_temp": 4.5},
        })
        self.assertEqual(data["climate_heat_pump"], "climate.hp")
        self.assertEqual(data["switch_circulation"], "")   # cleared → stored blank
        self.assertEqual(data["daily_hours"], 8)
        self.assertIsInstance(data["daily_hours"], int)
        self.assertEqual(data["pool_critical_temp"], 4.5)
        self.assertNotIn("advanced", data)

    def test_validate(self):
        ok = {"switch_rpm_low": "a", "switch_rpm_medium": "b", "switch_rpm_high": "c",
              "sensor_pool_temp": "p", "sensor_outdoor_temp": "o"}
        self.assertEqual(flow_mod._validate(ok), {})
        self.assertEqual(flow_mod._validate({**ok, "switch_rpm_high": "a"})["base"], "duplicate_speed_switch")
        self.assertEqual(flow_mod._validate({**ok, "sensor_outdoor_temp": "p"})["base"], "same_temperature_sensor")

    def test_schema_prefills_existing_install(self):
        schema = flow_mod._schema({**CFG, "temp_algae_threshold": 8.0}, with_name=False)
        keys = {str(k): k for k in schema}
        self.assertEqual(keys["switch_rpm_low"].description, {"suggested_value": "switch.low"})
        self.assertEqual(keys["temp_algae_threshold"].default, 8.0)
        _, optional, opts = schema[keys["optional_devices"]]
        self.assertFalse(opts["collapsed"])   # has a heat pump → shown open


if __name__ == "__main__":
    unittest.main()
