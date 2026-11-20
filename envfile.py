"""Load a local .env file without overriding the process environment.

Values already present in the process environment win. This module never
prints variable values.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from exposure.client import configured_key

# Names this dashboard will accept from .env. Anything else is ignored.
ALLOWED = {
    "SHODAN_API_KEY",
    "CARTO_BASEMAP_KEY",
    "DETROIT_SYNC_INTERVAL_SECONDS",
    "DETROIT_LOOKBACK_HOURS",
    "DETROIT_RECONCILE_HOURS",
    "DETROIT_RECONCILE_INTERVAL_SECONDS",
    "SHOTSPOTTER_SYNC_INTERVAL_SECONDS",
    "SHOTSPOTTER_LOOKBACK_HOURS",
}

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def tighten_permissions(path: Path) -> None:
    """Make a local env file readable only by its owner."""
    os.chmod(path, 0o600)


def load_env(path: Path) -> dict:
    """Apply allowed keys that the process does not already define.

    Returns which names were applied. It does not return values.
    """
    target = Path(path)
    if not target.is_file():
        return {"present": False, "applied": []}
    tighten_permissions(target)
    applied: list[str] = []
    text = target.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            continue
        name = match.group(1)
        if name not in ALLOWED or name in os.environ:
            continue
        os.environ[name] = _unquote(match.group(2).strip())
        applied.append(name)
    return {"present": True, "applied": applied}


def create_key_file(path: Path, key: str) -> dict:
    """Create a new env file containing only SHODAN_API_KEY.

    Refuses when the file already exists. The returned report has no key.
    """
    target = Path(path)
    if target.exists():
        raise FileExistsError("refusing to overwrite existing env file")
    cleaned = configured_key(key)
    if not cleaned:
        return {"configured": False, "key_length": 0}
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, ("SHODAN_API_KEY=%s\n" % cleaned).encode("utf-8"))
    finally:
        os.close(descriptor)
    tighten_permissions(target)
    return {"configured": True, "key_length": len(cleaned)}


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "create":
        target = Path(argv[2])
        key = sys.stdin.readline().rstrip("\r\n")
        try:
            result = create_key_file(target, key)
        except FileExistsError:
            print("A .env file already exists. Refusing to overwrite it.", file=sys.stderr)
            return 2
        print("configured: %s" % ("true" if result["configured"] else "false"))
        if result["configured"]:
            print("key_length: %s" % result["key_length"])
        return 0 if result["configured"] else 1
    print("Usage: python3 -m envfile create PATH", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
