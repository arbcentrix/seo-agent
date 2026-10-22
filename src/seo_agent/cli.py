import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, ConfigError, load_config
from .smoke import run_smoke
from .state import DirStore, StateBranch, StateError

COMMANDS = ("daily", "watch", "weekly", "monthly", "qa", "smoke", "sync", "verify", "log")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seo_agent")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("target", nargs="?", type=Path,
                        help="qa: the file to check (alerts, recommendations, labels or email JSON)")
    parser.add_argument("--out", type=Path, default=ROOT / "out", help="output directory")
    parser.add_argument("--no-state", action="store_true",
                        help="do not write or push the state branch (local runs)")
    parser.add_argument("--fixtures", type=Path, help="crawl saved fixtures instead of the live site")
    parser.add_argument("--previous", type=Path,
                        help="previous snapshot.json to diff against (only with --no-state)")
    parser.add_argument("--skip-sources", action="store_true", help="daily: do not sync GSC/GA4/PSI")
    parser.add_argument("--only", default="gsc,ga4,psi", help="sync: comma-separated sources")
    parser.add_argument("--force-psi", action="store_true", help="run PSI even if run recently")
    parser.add_argument("--labels", type=Path, help="weekly: apply Claude's intents/scores; qa: check them")
    parser.add_argument("--recommendations", type=Path, help="qa: recommendations JSON to check")
    parser.add_argument("--email", type=Path, help="qa: email JSON to check")
    parser.add_argument("--weekly", type=Path, help="qa/verify: weekly.json (default out/weekly.json)")
    parser.add_argument("--items", type=Path, help="verify: Applied items JSON")
    parser.add_argument("--run", help="log: the run this line records (daily, watch, weekly, monthly)")
    parser.add_argument("--status", default="ok", help="log: ok | error | skipped")
    parser.add_argument("--note", help="log: short note (what failed, what was sent)")
    parser.add_argument("--suspicious", action="append", default=[],
                        help="log: untrusted text that read like instructions (repeatable)")
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

    if args.command == "log":
        if not args.run:
            parser.error("log needs --run")
        try:
            st = store()
            st.close_run(args.run, status=args.status, note=args.note,
                         suspicious=[{"source": "claude", "text": t[:200]} for t in args.suspicious])
            if not args.no_state:
                st.commit_and_push(f"{args.run}: {args.status}")
        except StateError as e:
            print(f"state error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.command == "verify":
        from .reporting import run_verify
        if not args.items:
            parser.error("verify needs --items")
        st = DirStore(args.out / "state-local") if args.no_state else StateBranch(ROOT, cfg.env.state_branch).open()
        return run_verify(cfg, st, args.out, args.items, args.weekly)

    if args.command == "qa":
        from .reporting import run_qa
        st = DirStore(args.out / "state-local") if args.no_state else StateBranch(ROOT, cfg.env.state_branch).open()
        recs, email, labels = args.recommendations, args.email, args.labels
        if args.target:
            from .reporting import detect_kind, run_qa_alerts
            kind = detect_kind(args.target)
            if kind == "alerts":
                return run_qa_alerts(cfg, args.target, args.out)
            if kind is None:
                print(f"qa: cannot tell what {args.target} contains", file=sys.stderr)
                return 2
            recs, email, labels = (args.target if kind == k else v for k, v in
                                   (("recommendations", recs), ("email", email), ("labels", labels)))
        return run_qa(cfg, st, args.out, recs, email, labels, args.weekly)

    if args.command == "weekly":
        from .reporting import run_weekly
        try:
            st = store()
            sync_result = None
            if not args.labels and not args.skip_sources:
                from .sync import run_sync
                sync_result = run_sync(cfg, st, force_psi=args.force_psi)

            def commit(kind, summary, sync_res):
                if args.no_state:
                    return
                extra = {"sources": sync_res["sources"]} if sync_res else {}
                st.append_run(kind, suspicious=(sync_res or {}).get("suspicious"), **summary, **extra)
                st.commit_and_push(f"{kind}: {json.dumps(summary)[:120]}")

            return run_weekly(cfg, st, args.out, args.labels, commit, sync_result)
        except StateError as e:
            print(f"state error: {e}", file=sys.stderr)
            return 1

    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
