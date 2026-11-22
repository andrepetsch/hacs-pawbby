"""Lightweight structural validation of the custom integration.

Covers the offline-checkable HACS/hassfest rules: manifest key order and
required keys, domain == folder, config_flow presence, platform files for
every PLATFORMS entry, strings/translations parity, services.yaml consistency,
hacs.json. Run in CI next to pytest.

Usage: python scripts/check_integration.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "pawbby"

VALID_IOT_CLASSES = {
    "assumed_state", "calculated", "cloud_polling", "cloud_push",
    "local_polling", "local_push",
}
VALID_INTEGRATION_TYPES = {"device", "helper", "hub", "service", "system"}

errors: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        errors.append(msg)


manifest = json.loads((CC / "manifest.json").read_text())
check(list(manifest) == sorted(manifest), "manifest.json keys are not alphabetically sorted")
for key in ("domain", "name", "version", "documentation", "iot_class", "integration_type"):
    check(key in manifest, f"manifest.json missing required key '{key}'")
check(manifest.get("domain") == CC.name, "manifest domain does not match folder name")
check(manifest.get("iot_class") in VALID_IOT_CLASSES, f"invalid iot_class {manifest.get('iot_class')}")
check(
    manifest.get("integration_type") in VALID_INTEGRATION_TYPES,
    f"invalid integration_type {manifest.get('integration_type')}",
)
check(
    manifest.get("config_flow") is True and (CC / "config_flow.py").exists(),
    "config_flow=true but config_flow.py missing",
)
for req in manifest.get("requirements", []):
    check(
        re.fullmatch(r"[A-Za-z0-9_.\-]+(==|>=|<=|~=|>|<)[A-Za-z0-9_.\-*]+", req) is not None,
        f"bad requirement specifier: {req}",
    )

init_src = (CC / "__init__.py").read_text()
m = re.search(r"PLATFORMS\s*:\s*list\[Platform\]\s*=\s*\[(.*?)\]", init_src, re.S)
check(m is not None, "PLATFORMS not found in __init__.py")
if m:
    for plat in re.findall(r"Platform\.(\w+)", m.group(1)):
        check((CC / f"{plat.lower()}.py").exists(), f"PLATFORMS lists {plat} but {plat.lower()}.py is missing")

strings = json.loads((CC / "strings.json").read_text())
for lang in ("en", "de"):
    path = CC / "translations" / f"{lang}.json"
    check(path.exists(), f"missing translation {lang}.json")
    if path.exists():
        t = json.loads(path.read_text())
        check(set(t) == set(strings), f"{lang}.json top-level keys differ from strings.json")
        check(
            set(t.get("config", {})) == set(strings.get("config", {})),
            f"{lang}.json 'config' sections differ from strings.json",
        )

services = yaml.safe_load((CC / "services.yaml").read_text())
check(
    set(services or {}) == set(strings.get("services", {})),
    "services.yaml entries differ from strings.json 'services' section",
)

hacs = json.loads((ROOT / "hacs.json").read_text())
check("name" in hacs, "hacs.json missing 'name'")

if errors:
    print("INTEGRATION CHECK FAILED:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("integration structure OK")
