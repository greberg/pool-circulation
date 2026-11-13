# Pool Circulation – Home Assistant Custom Component

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom component that automatically controls your pool circulation pump, UV lamp, and heat pump based on electricity prices. Runs at high speed during cheap hours, backs off during peak prices, always guarantees the daily minimum circulation target is met, and supports an extra filter mode for on-demand high-speed filtration.

---

## Features

| Feature | Details |
|---|---|
| **Summer and winter** | Two explicit seasons with their own rules — switch with `switch.pool_winter_mode` or the season toggle on the card |
| **Freeze guards** | Pool water near 3°C or outdoor frost → continuous circulation, regardless of price, timers or manual mode, in any season |
| **Price-optimised summer** | Cheapest hours at medium speed. Winter runs by temperature only |
| **Restart-safe** | Runtime, season, timers and today's prices are persisted; devices are re-checked every minute and re-applied after a controller reboot |
| **Algae skip** | Below the algae threshold (default 7°C pool water) no filtration hours are needed |
| **Extra filter mode** | High speed for a configurable time, survives HA restarts |
| **Cover burst** | 5 min high speed when the pool cover is opened |
| **Heat pump (summer)** | Kept on, target temperature by price. Winter: never touched |
| **UV lamp control** | On while the pump runs and the cover is not open |
| **Pump cooldown / min on-time** | Prevents rapid on/off cycling |
| **Lovelace cards** | Status card and day-plan card bundled with the integration — no extra install |
| **15-minute prices** | Quarter-hour price lists (Nordpool since Oct 2025) are averaged per hour |
| **HACS compatible** | Install and update via HACS |

---

## How It Works

Every minute (and immediately when a temperature, price window or device changes) the integration decides a mode — `off`, `low`, `medium` or `high` — and brings the switches in line with it. Only switches that differ are commanded, so nothing is spammed.

The sensor `sensor.pool_circulation_reason` always says, in plain words, why the pump is doing what it does.

### Safety guards — both seasons, always first

| Guard | Condition | Pump |
|---|---|---|
| **Pool critical** | Pool water ≤ critical temp (default **4°C** — circulation can fail approaching 3°C) | `low` continuously, `medium` when outdoor is below the medium limit (default **0°C**) |
| **Frost guard** | Outdoor ≤ frost guard (default **2°C**) | `low`, continuously — protects pipes, pump and filter even when the pool water is not at risk. Set it very low if your plumbing is protected |
| **Sensor failsafe** | Winter and no outdoor temperature reading | `low`, continuously |

Guards ignore price, cooldown, minimum on-time and the automation switch. They release only once the reading is 0.5°C above the threshold, so the pump does not flap. Extra filter and the cover burst still win, since they run faster anyway.

### Summer

1. Pool water below the algae threshold (default 7°C) → `off` (the frost guard still applies)
2. Cheapest *daily hours* (default 8) → `medium`, heat pump at its best-price target
3. Every other hour → `low` — a warm pool is never left standing
4. Behind on daily hours late in the day → catch up at `medium` (only `low` during the day's 4 priciest hours)
5. No price list for today → medium until the daily hours are done, then low

### Winter

The heat pump is left completely alone (power it down yourself). Price is ignored — circulation depends on the pool temperature only, with the safety guards above on top:

| Pool water | Outdoor | Pump |
|---|---|---|
| ≥ algae threshold (7°C) | any | `low`, around the clock |
| between critical (4°C) and algae threshold (7°C) | above the frost guard (2°C) | `off` — no algae, no freezing risk |
| between critical and algae threshold | ≤ frost guard | `low` (frost guard) |
| ≤ critical (4°C) | ≥ medium limit (0°C) | `low` |
| ≤ critical | below medium limit | `medium` |

Each limit has 0.5°C hysteresis: a running pump stops only when the pool drops to 6.5°C, the critical guard releases at 4.5°C, and medium drops back to low at 0.5°C outdoor.

### Restarts

- Runtime is counted from the time the pump switch is actually on, and saved every 5 minutes. Time while HA was down is not counted.
- Season, automation switch, extra filter end time, cover burst end time, safety guard state and today's price list are stored and restored.
- Nothing is commanded until HA has fully started. Temperature sensors get up to 2 minutes to report before the integration decides without them.
- Every minute the switches are compared with the wanted mode. A pump controller that reboots and comes back off is turned on again as soon as it reappears.
- With automation off, devices are left exactly as you set them — unless a safety guard is active.

---

## Dashboard cards

The integration ships two Lovelace cards and loads them automatically (no HACS frontend install, no resources to add). After updating, reload the browser once.

```yaml
type: custom:pool-circulation-card
```
Season toggle, current mode, the reason, freeze-guard banner, pool and outdoor temperature on scales with the critical / algae / frost / cold thresholds, today's progress, and buttons for automation and extra filter.

```yaml
type: custom:pool-circulation-plan-card
```
Today's price per hour as bars, coloured by the planned speed for that hour, with the current hour outlined.

Both find the integration automatically. Add `entity: sensor.pool_circulation_mode` if you have renamed it, and `title:` to change the heading.

---

## Prerequisites

- Home Assistant 2024.8 or newer
- **Required:** three switches for the pump speeds (low / medium / high), an electricity price sensor that lists today's prices as an attribute (Nordpool, Tibber and similar — hourly or 15-minute), a pool water temperature sensor and an outdoor temperature sensor
- **Optional:** a pump on/off (enable) switch, a pump feedback sensor (power or rpm), a heat pump (`climate`), a UV lamp switch, a pool cover

### Speed controller setups

The integration only assumes on/off switches. A typical setup is a dumb pump behind a speed controller (inverter) whose digital inputs are driven by an ESP:

| ESPHome switch | Use as |
|---|---|
| `Inverter On/off` | Pump on/off switch (optional devices) |
| `Inverter 1200rpm` / `2400rpm` / `2900rpm` | Low / medium / high speed switch |

When running, the speed is selected first and the controller enabled after; when stopping, the controller is disabled first. These switches only show what the ESP outputs — not what the pump does. Add a **pump feedback sensor** to know that:

- **Runtime** is counted from the feedback, not from the switches.
- **Pump fault**: if the pump has not followed the switches after 3 minutes, `binary_sensor.pool_pump_fault` turns on, a `pool_circulation_pump_fault` event fires, and the outputs are re-sent (enable pulsed off → on) every 10 minutes until the pump responds.
- Values above *Pump feedback: running above* (default 10) count as running, so a power sensor (W) and an rpm sensor both work.
- **Actual speed**: the feedback is also mapped to low / medium / high, and the card shows that speed instead of the selected one. Automatic limits: from 300 W medium and from 500 W high, or from 2000 rpm medium and from 2650 rpm high. Other units, or other pumps, set *medium speed from* / *high speed from* under Advanced.
- **Start-up ramp**: many speed controllers start at full speed and settle on the selected speed after a few minutes. For 6 minutes after a speed change a different measured speed shows as "Start-up · settling to Low" on the card, not as a fault. If it still differs after that (plus the 3-minute grace), the fault is `wrong_speed`.

Use the power sensor of the plug directly if you can. A template that turns "plug unavailable" into `0` makes a lost plug look like a stopped pump and raises a false fault; when the feedback is `unavailable` the integration falls back to the switches instead.

---

## Installation via HACS

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/greberg/pool-circulation` as category **Integration**
3. Find **Pool Circulation** in the HACS list and click **Download**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search **Pool Circulation**
6. Pick your entities from the lists

---

## Configuration

One form, used both for setup and later under **Settings → Devices & Services → Pool Circulation → Configure**. Every entity is picked from a list of your existing entities.

| Field | Default | |
|---|---|---|
| Low / medium / high speed switch | — | Required, three different switches |
| Electricity price sensor | — | Required |
| Pool water temperature | — | Required |
| Outdoor temperature | — | Required |
| Summer filtration hours per day | 8 h | Cheapest hours run at medium speed |
| Algae threshold (pool) | 7°C | At or above: summer filters by price, winter runs low all day. Below: off |
| Frost guard (outdoor) | 2°C | At or below this the pump runs continuously at low |

**Optional devices** (collapsed): pump on/off switch, pump feedback sensor, heat pump, UV lamp, pool cover. Leave empty if you don't have them. An "actual RPM sensor" from an older version is moved here automatically.

**Advanced** (collapsed): critical pool temperature (4°C), critical pool: medium speed below outdoor (0°C), extra filter duration (60 min), cooldown and minimum on-time (10 / 10 min), heat pump targets (31 / 30°C), pump feedback running threshold (10), pump feedback medium / high speed limits (0 = automatic by unit).

Changing an entity reloads the integration. Changing a setting takes effect right away without touching today's runtime.

---

## Entities Created

### Sensors
| Entity | Description |
|---|---|
| `sensor.pool_circulation_mode` | `off` / `low` / `medium` / `high` — attributes include `season`, `rule`, `reason`, `safety`, `winter_tier`, temperatures, price, `is_peak_price`, `target_hours`, `done_hours`, `actual_speed` (measured by the feedback sensor), `speed_settling`, `feedback_value`, `unavailable_entities` and `plan` (today per hour, used by the cards) |
| `sensor.pool_circulation_reason` | Why the pump is in its current mode, in plain words |
| `sensor.pool_circulation_hours_today` | Hours the pump has run today, all speeds |
| `sensor.pool_circulation_hours_remaining` | Hours still needed today for the current season |

### Binary sensor
| Entity | Description |
|---|---|
| `binary_sensor.pool_pump_fault` | On when the pump does not follow the switches (`fault`: `not_running` / `not_stopping` / `wrong_speed`). Needs a feedback sensor |

### Switches
| Entity | Description |
|---|---|
| `switch.pool_winter_mode` | Season — on = winter rules (heat pump never touched), off = summer rules |
| `switch.pool_circulation_automation` | Off = leave the pump as you set it (freeze guards still apply) |
| `switch.pool_extra_filter` | High speed for the extra filter duration, then switches itself off |

### Numbers
| Entity | Description |
|---|---|
| `number.pool_circulation_daily_hours` | Summer filtration hours per day |
| `number.pool_algae_threshold` | Algae threshold (pool) |
| `number.pool_frost_threshold` | Frost threshold (outdoor) |
| `number.pool_extra_filter_duration` | Extra filter duration |

Upgrading from an older version removes the entities that are no longer created (RPM, price, heat pump and temperature mirrors, and the extra number entities — those settings are now under Configure → Advanced).

---

## Events

### `pool_circulation_mode_changed`
Fired whenever the circulation mode changes:
```yaml
previous_mode: medium
mode: high
season: summer
rule: extra_filter
reason: Extra filter running at high speed
hours_run_today: 5.25
daily_target: 8
```

### `pool_circulation_uv_changed`
Fired whenever the UV lamp is turned on or off:
```yaml
uv_on: true
mode: high
cover_open: false
```

### `pool_circulation_pump_fault`
Fired when a pump fault starts and when it clears:
```yaml
fault: not_running     # or not_stopping, or null when cleared
active: true
mode: low
feedback: 0.0
```

### `pool_circulation_extra_filter_changed`
Fired when extra filter mode is activated or deactivated (including auto-timeout):
```yaml
active: true
duration_minutes: 60
```

Use these events to trigger push notifications without any logic in YAML:

```yaml
- alias: Notify on extra filter done
  trigger:
    - platform: event
      event_type: pool_circulation_extra_filter_changed
      event_data:
        active: false
  action:
    - service: notify.mobile_app_peter_iphone
      data:
        message: "Extra filtration complete!"
```

---

## Credits

Built for a Höllviken pool running an Aqua-Temp heat pump and ESPHome-controlled circulation pump, using the Trulsibrunn Nordpool electricity price integration.

---

## Development

The decision logic lives in `logic.py` and price parsing in `prices.py` — plain Python with no Home Assistant imports. Tests (no HA install needed):

```bash
python3 -m unittest discover -s tests
```
