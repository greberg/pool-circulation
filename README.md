# Pool Circulation – Home Assistant Custom Component

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom component that automatically controls your pool circulation pump, UV lamp, and heat pump based on electricity prices. Runs at high speed during cheap hours, backs off during peak prices, always guarantees the daily minimum circulation target is met, and supports an extra filter mode for on-demand high-RPM filtration.

---

## Features

| Feature | Details |
|---|---|
| **Price-based scheduling** | Runs at high RPM + heat pump during best-price windows; stops during peak prices |
| **Daily hours guarantee** | Must-run override kicks in when the daily target can't be met any other way |
| **Algae skip** | Skips circulation when pool water temp is below the algae growth threshold |
| **Freeze protection** | Forces low-speed circulation when outdoor temp drops to freeze threshold — overrides everything |
| **Extra filter mode** | On-demand switch that forces high RPM for a configurable duration, then auto-disables |
| **Pump cooldown** | Configurable minimum wait before pump can turn back on after stopping — prevents rapid on/off cycling |
| **UV lamp control** | Automatically turns UV lamp on when pump is running and pool cover is not open |
| **Heat pump control** | Turns heat pump on/off via any `climate` entity |
| **3-speed RPM control** | Maps low / medium / high RPM to individual switches; shows 0 when pump is off |
| **Persisted state** | Daily hours counter survives HA restarts; resets at midnight |
| **Automation switch** | Pause the scheduler without changing any other config |
| **Editable targets** | Adjust daily hours and extra filter duration from the HA UI without reconfiguring |
| **HACS compatible** | Install and update via HACS |

---

## How It Works

Every hour at HH:00, the coordinator evaluates price signals and sets one of four modes:

| Mode | Condition | Circulation | Heat pump | UV lamp |
|---|---|---|---|---|
| `low` | **Freeze protection** — outdoor temp ≤ freeze threshold | Low RPM ON | OFF | ON |
| `high` | Best-price period active **or** extra filter active | High RPM switch ON | ON (best price only) | ON |
| `medium` | Normal price, hours still needed | Medium RPM switch ON | OFF | ON |
| `off` | Peak price, daily target met, or **algae skip** | All switches OFF | OFF | OFF |

**Priority order (highest → lowest):**
1. **Freeze protection** — outdoor temp ≤ freeze threshold (default 2°C) → forces `low`, bypasses cooldown
2. **Extra filter active** → forces `high` regardless of price or schedule, bypasses cooldown
3. **Automation switch off** → holds current mode
4. **Algae skip** — pool water temp below algae threshold (default 8°C) → `off`
5. **Cooldown** — pump turned off recently → holds `off` until cooldown elapses (default 10 min)
6. **Price logic** — peak → `off`, best → `high`, normal → `medium` if hours still needed
7. **Must-run override** — hours needed ≥ hours left today → forces `medium` regardless of price

---

## Prerequisites

- Home Assistant 2023.1 or newer
- A heat pump integrated as a `climate` entity (e.g. `aqua_temp`)
- Outdoor temperature sensor (required for freeze protection)
- Pool water temperature sensor (optional, for algae skip)
- UV lamp switch entity (optional)
- Pool cover entity (optional, prevents UV lamp from turning on when cover is open)
- Circulation pump wired as:
  - One `switch` entity for on/off
  - Up to three `switch` entities for RPM levels (low / medium / high)
- Electricity price sensors — the integration defaults to the **Trulsibrunn** Nordpool setup:
  - `sensor.trulsibrunn_timpris_aktuell` — current hour price
  - `sensor.trulsibrunn_aktuell_timprisniva` — price level label
  - `binary_sensor.trulsibrunn_basta_prisperiod` — best price window
  - `binary_sensor.trulsibrunn_topprisperiod` — peak price window

Any Nordpool or Tibber-based price integration with equivalent entities works — just override the defaults in the config flow.

---

## Installation via HACS

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/greberg/pool-circulation` as category **Integration**
3. Find **Pool Circulation** in the HACS list and click **Download**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search **Pool Circulation**
6. Fill in the configuration form

---

## Configuration

### Setup (one-time)

| Field | Required | Description |
|---|---|---|
| Heat pump climate entity | ✅ | e.g. `climate.pool_heat_pump` |
| Circulation pump switch | ✅ | Main on/off switch |
| Low RPM switch | — | Optional |
| Low RPM value | — | RPM displayed when low switch is on (default 1000) |
| Medium RPM switch | — | Optional |
| Medium RPM value | — | RPM displayed when medium switch is on (default 2000) |
| High RPM switch | — | Optional |
| High RPM value | — | RPM displayed when high switch is on (default 3000) |
| Current price sensor | — | Default: `sensor.trulsibrunn_timpris_aktuell` |
| Price level sensor | — | Default: `sensor.trulsibrunn_aktuell_timprisniva` |
| Best price binary sensor | — | Default: `binary_sensor.trulsibrunn_basta_prisperiod` |
| Peak price binary sensor | — | Default: `binary_sensor.trulsibrunn_topprisperiod` |
| Daily circulation hours | ✅ | Target hours per day (0–24, default 8) |
| Outdoor temperature sensor | — | Used for freeze protection |
| Pool temperature sensor | — | Used for algae skip |
| Algae growth threshold | — | Default 8°C — skip circulation below this pool temp |
| Freeze protection threshold | — | Default 2°C — force low-speed circulation below this outdoor temp |
| Actual RPM sensor | — | Optional sensor reporting real inverter RPM (e.g. ESPHome). When set, overrides the switch-derived RPM value |
| UV lamp switch | — | Controlled automatically based on circulation state |
| Pool cover entity | — | UV lamp stays off when cover is open |
| Extra filter duration | — | Minutes to run after extra filter is activated (default 60, range 5–480) |
| Pump cooldown | — | Minutes to wait before pump can turn on again after stopping (default 10, 0 = disabled) |

### Options (editable after setup)

Go to **Settings → Devices & Services → Pool Circulation → Configure** to adjust:
- Daily circulation hours target
- Price signal entity IDs
- Outdoor and pool temperature sensors
- Algae growth threshold (°C)
- Freeze protection threshold (°C)
- Actual RPM sensor entity
- UV lamp switch and pool cover entity
- Extra filter duration (minutes)

---

## Entities Created

### Sensors
| Entity | Description |
|---|---|
| `sensor.pool_circulation_mode` | Current mode: `off` / `low` / `medium` / `high` — attributes include `too_cold`, `freeze_risk`, `in_cooldown`, `cooldown_remaining`, `extra_filter_active`, `uv_on`, temps, price |
| `sensor.pool_circulation_rpm` | Current RPM (numeric) — reads actual RPM sensor if configured, otherwise derived from active switch; `0` when pump is off |
| `sensor.pool_heat_pump_mode` | Current HVAC mode of the heat pump: `off` / `cool` / `heat` / `auto` |
| `sensor.pool_heat_pump_current_temperature` | Temperature reading from the heat pump — attributes include target temp and fan mode |
| `sensor.pool_circulation_hours_today` | Hours the pump has run today |
| `sensor.pool_circulation_hours_remaining` | Hours still needed to hit today's target |
| `sensor.pool_electricity_price` | Current electricity price (SEK/kWh) |
| `sensor.pool_electricity_price_level` | Price level label from your Nordpool integration |
| `sensor.pool_outdoor_temperature` | Outdoor temperature passthrough (hidden by default) |
| `sensor.pool_water_temperature` | Pool water temperature passthrough (hidden by default) |

### Switches
| Entity | Description |
|---|---|
| `switch.pool_circulation_automation` | Enable / disable price-based scheduling |
| `switch.pool_extra_filter` | Activate extra filter mode — forces high RPM for the configured duration, then auto-disables |

### Numbers
| Entity | Description |
|---|---|
| `number.pool_circulation_daily_hours` | Target circulation hours per day (editable in UI) |
| `number.pool_extra_filter_duration` | Duration of extra filter mode in minutes (editable in UI, default 60) |
| `number.pool_pump_cooldown` | Minimum minutes between pump off → on (0 = disabled, default 10) |

---

## Events

### `pool_circulation_mode_changed`
Fired whenever the circulation mode changes:
```yaml
previous_mode: medium
mode: high
hours_run_today: 5
daily_target: 8
```

### `pool_circulation_uv_changed`
Fired whenever the UV lamp is turned on or off:
```yaml
uv_on: true
mode: high
cover_open: false
active_rpm: 3000
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
