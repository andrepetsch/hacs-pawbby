"""The Pawbby Smart Litter Box integration.

Standalone Home Assistant custom component (distributed via HACS) that talks
directly to the Pawbby litter box over the local network (Tuya v3.4) —
no Pawbby-Reborn server instance required.

The Tuya DP protocol was reverse-engineered in the Pawbby-Reborn project
(AGPL-3.0); see VALUES.md / DP105_SETTINGS.md there. This integration is
licensed AGPL-3.0 as well.

NOTE: this module intentionally avoids Home Assistant imports for now so the
protocol layer stays unit-testable without HA. Setup via config flow is added
in a later phase.
"""

from .const import DOMAIN

__all__ = ["DOMAIN"]
