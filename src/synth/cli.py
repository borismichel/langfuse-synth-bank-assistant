"""The kit's `synth` runtime CLI (walking skeleton).

Registers the pipeline verbs the manifest wires — `seed` and `verify` — each running
through the shared library. The invocation these verbs answer is the Contract's:
`synth <verb> --config {config}`, with `--set dotted.key=value` overrides appended on
pipeline steps only (CONTRACT.md §"The container invocation" — live and resume commands
never receive `--set`). A step id that is a reserved verb (seed/verify/...) must run
`synth <that verb>` — the manifest keeps that contract, so grow new verbs here and in
`usecase.yaml` together.
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .seed import run_seed
from .verify import run_verify
from .pipeline import run_plan, run_probe, run_import
from .setup import run_setup_prompts
from .assets import run_setup_assets
from .cohorts import run_prepare_cohorts


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="path to the kit config YAML")
    parser.add_argument(
        "--set",
        action="append",
        metavar="dotted.key=value",
        help="override a config value (repeatable); applied before validation",
    )


def _cmd_seed(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, overrides=args.set)
    run_seed(cfg, dry_run=args.dry_run)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, overrides=args.set)
    return 0 if run_verify(cfg, timeout_seconds=args.timeout_seconds).ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="synth", description="Demo Depot synth kit runtime.")
    sub = parser.add_subparsers(dest="command", metavar="<verb>")

    seed = sub.add_parser("seed", help="generate + ingest the backdated demo data")
    _add_config_args(seed)
    seed.add_argument("--dry-run", action="store_true", help="spool only; no network")
    seed.set_defaults(func=_cmd_seed)

    verify = sub.add_parser("verify", help="read the data back and assert the floor checks")
    _add_config_args(verify)
    verify.add_argument("--timeout-seconds", type=float, default=60)
    verify.set_defaults(func=_cmd_verify)

    for verb, function in (("setup-assets", run_setup_assets), ("setup-prompts", run_setup_prompts), ("probe", run_probe), ("plan", run_plan), ("import-spool", run_import)):
        command = sub.add_parser(verb)
        _add_config_args(command)
        command.set_defaults(func=lambda args, f=function: f(load_config(args.config, overrides=args.set)))
    cohorts = sub.add_parser("prepare-cohorts")
    _add_config_args(cohorts)
    cohorts.add_argument("--timeout-seconds", type=float, default=60)
    cohorts.set_defaults(func=lambda args: run_prepare_cohorts(
        load_config(args.config, overrides=args.set), timeout_seconds=args.timeout_seconds))
    generate = sub.add_parser("generate-spool")
    _add_config_args(generate)
    generate.set_defaults(func=lambda args: (run_seed(load_config(args.config, overrides=args.set), dry_run=True), 0)[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    actual = sys.argv[1:] if argv is None else argv
    if actual and actual[0] == "companion":
        from .companion.app import main as serve
        return serve(actual[1:])
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not hasattr(args, "func"):  # no subcommand given
        parser.print_help()
        return 2
    print(f"Starting {args.command}", flush=True)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
