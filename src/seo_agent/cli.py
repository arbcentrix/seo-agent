import argparse
import sys

COMMANDS = ("daily", "watch", "weekly", "monthly", "qa", "smoke")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="seo_agent")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv)
    print(f"{args.command}: not implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
