from __future__ import annotations

import argparse
import json
import signal
import threading
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
    serve = subparsers.add_parser("serve-simulated")
    for action in simulate._actions:
        if action.dest not in {"help"}:
            serve._add_action(action)
    serve.add_argument("--interval", type=float, default=60.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    load_configuration(args.config)
    if args.command == "serve-simulated":
        if args.interval <= 0:
            raise SystemExit("--interval must be greater than zero")
        stopped = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signum, _frame: stopped.set())
        while not stopped.is_set():
            _print_simulation(args.schema, args.authorization)
            stopped.wait(args.interval)
        return 0
    _print_simulation(args.schema, args.authorization)
    return 0


def _print_simulation(schema: Path, authorization: Path) -> None:
    for event in run_vertical_slice(schema, authorization):
        print(json.dumps(event, sort_keys=True), flush=True)
