# Pawbby Smart Litter Box (HACS)

Standalone [Home Assistant](https://www.home-assistant.io/) custom component,
distributed via [HACS](https://hacs.xyz/), that controls a **Pawbby Smart Cat
Litter Box** directly over your local network (Tuya protocol v3.4) — no cloud,
and **no Pawbby-Reborn server instance required**.

The Tuya DP protocol was reverse-engineered and hardware-confirmed in the
[Pawbby-Reborn](https://github.com/larsjarred9/Pawbby-Reborn) project
(see `VALUES.md` / `DP105_SETTINGS.md` there). This integration is a Python
port of that protocol layer and is licensed **AGPL-3.0**.

## Features

- **Sensors**: status, waste bin, litter level, visits today, last visit
  weight / pet / time, last cleaned, deodorizer days left — plus per-cat
  weight, duration, last used and visits today
- **Binary sensors**: online, cleaning, lid open, bin full/removed, drum
  removed, litter low, motor error
- **Buttons**: clean, flatten, empty, zero scale (tare), cancel clean
- **Switches**: auto-clean, quiet period, soft clumps mode, auto screen-off,
  screen lock
- **Number**: auto-clean delay (1–60 min) · **Select**: litter type
- **Events**: every litter-box event (visit, clean, bin full, …) is fired on
  the `pawbby_event` bus and shown as an event entity — build automations
  natively (this replaces Pawbby-Reborn's webhooks)
- **PawID**: visits are attributed to the configured cat whose profile weight
  is closest (within 300 g)

## Setup

1. Add this repository as a **custom repository** in HACS (category:
   *Integration*), then install "Pawbby Smart Litter Box" and restart HA.
2. Go to **Settings → Devices & Services → Add Integration → Pawbby**.
3. Enter the Tuya `device id` and `local key` of your litter box (the same
   values the Pawbby-Reborn setup wizard extracts), optionally its IP address.
   The connection is tested live during setup.
4. Add your cats with their profile weights (used for visit attribution).

The box's time zone and weight unit automatically follow your Home Assistant
configuration and are pushed to the device on every connect.

## Services

| Service | Fields | Description |
|---|---|---|
| `pawbby.action` | `device_id`, `action` (`clean`/`flatten`/`empty`/`cancel_clean`) | Motor command; refused while the box is busy or a cat is inside |
| `pawbby.tare` | `device_id` | Zero the scale (DP 109) |
| `pawbby.set_setting` | `device_id`, `setting`, `value` | Change a hardware setting on the box (DP 105): `auto_clean`, `sleep_mode`, `soft_clumps`, `auto_off_screen`, `child_lock` (bool), `auto_clean_delay` (1–60), `sleep_window` (`{"start": "22:00", "stop": "08:30"}`), `litter_type` (0–3 or name), `reset_deodorant`, `refresh` |

## Automations

Every event fires `pawbby_event` on the bus, e.g.:

```yaml
automation:
  - alias: "Notify when the waste bin is full"
    triggers:
      - trigger: event
        event_type: pawbby_event
        event_data:
          type: bin-full
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "Pawbby waste bin is full!"
  - alias: "Notify which cat used the box"
    triggers:
      - trigger: event
        event_type: pawbby_event
        event_data:
          type: toileted
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            {{ trigger.event.data.pet_id }} used the litter box
            ({{ trigger.event.data.weight_kg }} kg,
            {{ trigger.event.data.duration_s }} s).
```

## Publishing to HACS

The repository must have a description and at least one valid topic on
GitHub (the HACS `check-repository` validation fails otherwise). Set them
once with the GitHub CLI:

```bash
gh repo edit andrepetsch/hacs-pawbby \
  --description "Standalone Home Assistant (HACS) integration for the Pawbby smart cat litter box — 100% local Tuya control, no cloud, no Pawbby-Reborn server needed." \
  --add-topic home-assistant --add-topic hacs --add-topic hacs-integration \
  --add-topic tuya --add-topic pawbby --add-topic litter-box
```

Brand assets are shipped in-repo at `custom_components/pawbby/brand/`
(`icon.png` 256×256, `logo.png`), so no entry in the central brands
repository is needed.

## Development

Managed with [uv](https://docs.astral.sh/uv/). The repo is not a pip package
(`package = false`); uv only maintains the dev venv. The integration's runtime
dependency `tinytuya` is declared in `custom_components/pawbby/manifest.json`
and installed by Home Assistant itself — for local dev it's in the `dev`
dependency group.

```bash
uv sync                                      # install dev deps from uv.lock
uv run python scripts/check_integration.py   # structural HACS/hassfest checks
uv run pytest                                # 128 tests incl. HA harness
```

Protocol tests run against the hardware-confirmed vectors from Pawbby-Reborn
(`DP105_SETTINGS.md` / `VALUES.md`); HA-level tests use
`pytest-homeassistant-custom-component` with a fake Tuya device.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
