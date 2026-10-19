"""Make the small update patch for a release (used by the Release workflow).

    python scripts/make_patch.py OLD.exe NEW.exe OUT.patch

The patch is only written when it is clearly smaller than the full exe and
it rebuilds NEW.exe exactly; otherwise nothing is written and users simply
download the full exe.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from features import delta_patch  # noqa: E402

MAX_SHARE = 0.5  # a patch bigger than half the exe isn't worth it


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    old_path, new_path, out_path = (Path(value) for value in argv)
    old, new = old_path.read_bytes(), new_path.read_bytes()
    patch = delta_patch.make_patch(old, new)
    share = len(patch) / max(1, len(new))
    print(f"Patch {old_path.name} -> {new_path.name}: {len(patch) / 1024 / 1024:.2f} MB "
          f"({share:.1%} of {len(new) / 1024 / 1024:.1f} MB)")
    if delta_patch.apply_patch(old, patch) != new:
        print("The patch does not rebuild the new exe exactly; not publishing it.")
        return 0
    if share > MAX_SHARE:
        print("The patch is too large to be worth it; not publishing it.")
        return 0
    out_path.write_bytes(patch)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
