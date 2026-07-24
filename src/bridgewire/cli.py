from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from bridgewire import __version__
from bridgewire.configuration import load_configuration
from bridgewire.simulation import run_vertical_slice


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridgewire")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    simulate = subparsers.add_parser("simulate")
    simulate.add_argument(
        "--config",
        type=Path,
        default=Path("configs/simulation.toml"),
    )
    simulate.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/authorization-file/schema.json"),
    )
    simulate.add_argument(
        "--authorization",
        type=Path,
        default=Path("configs/simulation-authorization.csv"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    load_configuration(args.config)
    events = run_vertical_slice(args.schema, args.authorization)
    for event in events:
        print(json.dumps(event, sort_keys=True))
    return 0
