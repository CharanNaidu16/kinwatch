"""Choose which recorded takes are kept in the repo for replay.

    python scripts/sessions.py list
    python scripts/sessions.py keep take-2 --as sofa-demo --from 40 --to 250 --title "Sofa: alert and claim"
    python scripts/sessions.py drop sofa-demo

Takes live in data/sessions/ (gitignored, recorded with "record start" / "record stop" in the chat).
"keep" copies one to fixtures/sessions/ (committed to the repo), trimmed to --from/--to seconds,
with every family name replaced by a sample name (--name Charan=Priya to choose them). Real family
names never reach the repo.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings  # noqa: E402
from backend.sessions import KEPT_DIR, TAKES_DIR, keep, list_sessions, sample_names, valid_name  # noqa: E402


def _mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="recorded takes and kept sessions")
    k = sub.add_parser("keep", help="copy a take into the repo")
    k.add_argument("take")
    k.add_argument("--as", dest="kept_name", help="name in the repo (default: the take's name)")
    k.add_argument("--from", dest="start", type=float, default=0.0, help="start at this many seconds")
    k.add_argument("--to", dest="end", type=float, help="end at this many seconds")
    k.add_argument("--title", help="shown on the replay page")
    k.add_argument("--name", dest="names", action="append", default=[], metavar="REAL=SAMPLE",
                   help="choose a sample name")
    d = sub.add_parser("drop", help="remove a kept session from the repo")
    d.add_argument("name")
    args = ap.parse_args()

    settings = Settings()
    takes_dir = settings.data_path / TAKES_DIR
    if args.cmd == "list":
        for label, root in (("Recorded takes (private)", takes_dir), ("Kept in the repo", KEPT_DIR)):
            print(label + ":")
            rows = list_sessions(root)
            for s in rows:
                print(f"  {s['name']:<20} {_mmss(s.get('duration_sec', 0)):>6}  {s.get('messages', 0):>3} messages"
                      f"  {s['size_mb']} MB  {s.get('title', '')}")
            if not rows:
                print("  (none)")
        return

    if args.cmd == "drop":
        target = KEPT_DIR / args.name
        if not valid_name(args.name) or not target.is_dir():
            sys.exit(f"No kept session called {args.name}.")
        shutil.rmtree(target)
        print(f"Removed {args.name} from the repo's sessions.")
        return

    src = takes_dir / args.take
    name = args.kept_name or args.take
    if not valid_name(args.take) or not (src / "session.json").exists():
        sys.exit(f"No recorded take called {args.take}. See: python scripts/sessions.py list")
    if not valid_name(name):
        sys.exit("Use lowercase letters, digits and dashes for --as.")
    overrides = dict(pair.split("=", 1) for pair in args.names if "=" in pair)
    names = sample_names([m.name for m in settings.family_members_json], overrides)
    out = keep(src, KEPT_DIR / name, names, args.start, args.end, args.title)
    print(f"Kept {args.take} as {name}: {_mmss(out['duration_sec'])}, {out['frames']} frames, "
          f"{out['messages']} messages.")
    print("Names in the kept copy: " + ", ".join(f"{real} -> {sample}" for real, sample in names.items()))
    print("Check it on the behind-the-scenes page, then commit fixtures/sessions/" + name)


if __name__ == "__main__":
    main()
