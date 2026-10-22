import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, ConfigError, load_config
from .smoke import run_smoke
from .state import StateBranch, StateError

COMMANDS = ("daily", "watch", "weekly", "monthly", "qa", "smoke")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seo_agent")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="output directory")
    parser.add_argument("--no-state", action="store_true",
                        help="do not write or push the state branch (local runs)")
    parser.add_argument("--fixtures", type=Path, help="crawl saved fixtures instead of the live site")
    parser.add_argument("--previous", type=Path,
                        help="previous snapshot.json to diff against (only with --no-state)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.command == "smoke":
        return 0 if run_smoke(cfg) else 1

    if args.command in ("daily", "watch"):
        from .scan import run_scan
        if args.previous and not args.no_state:
            parser.error("--previous needs --no-state (the state branch supplies it otherwise)")
        previous = json.loads(args.previous.read_text()) if args.previous else None
        try:
            state = None if args.no_state else StateBranch(ROOT, cfg.env.state_branch).open()
            run_scan(args.command, cfg, args.out, state, args.fixtures, previous)
        except StateError as e:
            print(f"state error: {e}", file=sys.stderr)
            return 1
        return 0

    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
