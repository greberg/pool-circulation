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
    mod("homeassistant.const", EVENT_HOMEASSISTANT_STARTED="homeassistant_started")
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


_install_stubs()
_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "pool_circulation"
_pkg = types.ModuleType("pcc")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["pcc"] = _pkg
coord_mod = importlib.import_module("pcc.coordinator")

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
    "binary_best_price": "binary_sensor.best",
    "binary_peak_price": "binary_sensor.peak",
    "sensor_outdoor_temp": "sensor.outdoor",
    "sensor_pool_temp": "sensor.pool",
    "cooldown_minutes": 0,
    "min_on_minutes": 0,
}


def make_hass(outdoor="3", pool="5.5"):
    hass = FakeHass()
    for e in ("switch.pump", "switch.low", "switch.medium", "switch.high", "switch.uv"):
        hass.states.set(e, "off")
    hass.states.set("climate.hp", "heat", {"temperature": 30})
    hass.states.set("sensor.price", "0.6", {"today": PRICES})
    hass.states.set("binary_sensor.best", "off")
    hass.states.set("binary_sensor.peak", "off")
    hass.states.set("sensor.outdoor", outdoor)
    hass.states.set("sensor.pool", pool)
    return hass


async def start(hass, store_winter=None):
    entry = types.SimpleNamespace(entry_id="e1", data=dict(CFG), options={})
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

    def test_winter_cold_runs_low_and_leaves_heat_pump_alone(self):
        async def go():
            hass = make_hass(outdoor="3", pool="5.5")
            c = await start(hass, store_winter=True)
            self.assertEqual(c.current_mode, "low")
            self.assertTrue(on(hass, "switch.pump") and on(hass, "switch.low"))
            self.assertFalse(on(hass, "switch.medium"))
            self.assertFalse([x for x in hass.calls if x[0] == "climate"])
            self.assertEqual(c.data["winter_tier"], "cold")
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
            hass = make_hass(outdoor="5", pool="6")
            c = await start(hass, store_winter=True)
            await c.async_set_automation(False)
            hass.set_state("sensor.pool", "3.6")
            await hass.settle()
            self.assertEqual(c.current_mode, "medium")
            self.assertTrue(on(hass, "switch.medium"))
            self.assertEqual(c.data["safety"], "pool_critical")
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


if __name__ == "__main__":
    unittest.main()
