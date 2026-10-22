import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, ConfigError, load_config
from .smoke import run_smoke
from .state import DirStore, StateBranch, StateError

COMMANDS = ("daily", "watch", "weekly", "monthly", "qa", "smoke", "sync")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seo_agent")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="output directory")
    parser.add_argument("--no-state", action="store_true",
                        help="do not write or push the state branch (local runs)")
    parser.add_argument("--fixtures", type=Path, help="crawl saved fixtures instead of the live site")
    parser.add_argument("--previous", type=Path,
                        help="previous snapshot.json to diff against (only with --no-state)")
    parser.add_argument("--skip-sources", action="store_true", help="daily: do not sync GSC/GA4/PSI")
    parser.add_argument("--only", default="gsc,ga4,psi", help="sync: comma-separated sources")
    parser.add_argument("--force-psi", action="store_true", help="run PSI even if run recently")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.command == "smoke":
        return 0 if run_smoke(cfg) else 1

    def store():
        if args.no_state:
            return DirStore(args.out / "state-local")
        return StateBranch(ROOT, cfg.env.state_branch).open()

    if args.command == "sync":
        from .sync import run_sync
        try:
            st = store()
            result = run_sync(cfg, st, [s.strip() for s in args.only.split(",") if s.strip()],
                              args.force_psi)
            if not args.no_state:
                st.append_run("sync", suspicious=result["suspicious"], sources=result["sources"])
                st.commit_and_push("sync sources")
        except StateError as e:
            print(f"state error: {e}", file=sys.stderr)
            return 1
        return 1 if any("error" in v for v in result["sources"].values()) else 0

    if args.command in ("daily", "watch"):
        from .scan import run_scan
        if args.previous and not args.no_state:
            parser.error("--previous needs --no-state (the state branch supplies it otherwise)")
        previous = json.loads(args.previous.read_text()) if args.previous else None
        try:
            state = None if args.no_state else store()
            sync_result = None
            if args.command == "daily" and not args.skip_sources and not args.fixtures:
                from .sync import run_sync
                sync_result = run_sync(cfg, state or store(), force_psi=args.force_psi)
            run_scan(args.command, cfg, args.out, state, args.fixtures, previous,
                     sync_result=sync_result)
        except StateError as e:
            print(f"state error: {e}", file=sys.stderr)
            return 1
        return 0

    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
