from __future__ import annotations

import argparse
from pathlib import Path

from access_control import __version__
from access_control.config import ConfigurationError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="access-control")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the installed release")
    check = commands.add_parser("check-config", help="validate a configuration file")
    check.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    try:
        load_config(args.config)
    except ConfigurationError as exc:
        print(f"invalid configuration: {exc}")
        return 2
    print("configuration valid")
    return 0
