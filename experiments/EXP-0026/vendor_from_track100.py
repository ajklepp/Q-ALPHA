#!/usr/bin/env python3
"""
Copy frozen Track 100 filter + C_ratchet modules into this experiment.

Never invents thresholds. Fails closed if the sibling repo is missing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
VENDOR = EXP_DIR / "vendor_track100"
REPO = EXP_DIR.parents[1]

COPY_NAMES = (
    "paper_filter.py",
    "paper_exit.py",
    "trail_exits.py",
    "walkforward_5k.py",
    "ops_stack_5k.py",
)


def _candidate_roots() -> list[Path]:
    out: list[Path] = []
    env = (os.environ.get("TRACK100_ROOT") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    for name in ("track-100", "Track 100", "Track100"):
        out.append(REPO.parent / name)
        out.append(REPO / name)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main() -> int:
    roots = [p for p in _candidate_roots() if p.is_dir()]
    if not roots:
        print("FAIL: no Track 100 root. Set TRACK100_ROOT or clone sibling track-100.")
        print("Tried:")
        for p in _candidate_roots():
            print(f"  {p}")
        return 2

    found: dict[str, Path] = {}
    used_root: Path | None = None
    for root in roots:
        hits = [n for n in COPY_NAMES if (root / n).is_file()]
        if hits:
            used_root = root
            for n in hits:
                found[n] = root / n
            break
        # Common layouts: repo/src, repo/paper, repo/ops
        for sub in ("", "src", "paper", "ops", "lib"):
            base = root / sub if sub else root
            for n in COPY_NAMES:
                p = base / n
                if p.is_file() and n not in found:
                    found[n] = p
                    used_root = root

    need_filter = any(p.name == "paper_filter.py" for p in found.values())
    need_exit = any(p.name in {"paper_exit.py", "trail_exits.py"} for p in found.values())
    if not need_filter or not need_exit:
        print("FAIL: Track 100 root found but missing paper_filter.py or exit module.")
        print(f"  root={used_root}")
        print(f"  found={sorted(found)}")
        return 3

    VENDOR.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    for name, src in found.items():
        dest = VENDOR / name
        shutil.copy2(src, dest)
        copied.append({
            "name": name,
            "src": str(src),
            "sha256": _sha256(dest),
            "bytes": str(dest.stat().st_size),
        })
        print(f"copied {name} <- {src}")

    manifest = {
        "copied_at_utc": datetime.now(timezone.utc).isoformat(),
        "track100_root": str(used_root),
        "files": copied,
        "note": "Frozen snapshot. Do not recut IS medians on OOS.",
    }
    (VENDOR / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    print(f"wrote {VENDOR / 'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
