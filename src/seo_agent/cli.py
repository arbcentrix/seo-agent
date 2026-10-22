import argparse
import sys

from .config import ConfigError, load_config
from .smoke import run_smoke

COMMANDS = ("daily", "watch", "weekly", "monthly", "qa", "smoke")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seo_agent")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.command == "smoke":
        return 0 if run_smoke(cfg) else 1

    print(f"{args.command}: not implemented yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
