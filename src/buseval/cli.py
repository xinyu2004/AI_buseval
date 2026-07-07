"""buseval CLI entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .loader import load_topology
from .engine.predictor import predict
from .engine.margin import evaluate_margin
from .lint import lint
from .report.terminal import render_terminal, render_health_terminal
from .report.structured import build_structured, dump_yaml, dump_json
from .dbc.health_report import build_health_report

PRESETS_DIR = Path(__file__).parent / "presets"


def _list_presets() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def _load_soc(name: str, dbc_path: str | None, can_dbc_mappings: list[tuple[str, str]] | None):
    p = PRESETS_DIR / f"{name}.yaml"
    if not p.exists():
        raise SystemExit(
            f"Unknown SoC preset '{name}'. Available: {', '.join(_list_presets())}"
        )
    topo = load_topology(p)
    _apply_dbcs(topo, dbc_path, can_dbc_mappings)
    return topo


def _apply_dbcs(topology, dbc_path: str | None, can_dbc_mappings: list[tuple[str, str]] | None):
    """Inject DBC(s) into the topology.

    - dbc_path (legacy --dbc): replace the first CAN master (regardless of enabled).
    - can_dbc_mappings (--can-dbc NAME=PATH): replace each named CAN master.
    Either way, the injected can_dbc master is force-enabled (user explicitly
    provided a DBC), so presets can keep CAN disabled by default.
    """
    if dbc_path and can_dbc_mappings:
        raise SystemExit("Use either --dbc or --can-dbc, not both.")
    if dbc_path:
        _inject_first_can(topology, dbc_path)
    elif can_dbc_mappings:
        _inject_named_cans(topology, can_dbc_mappings)


def _inject_first_can(topology, dbc_path: str):
    """Replace the first CAN master (regardless of enabled state) with a can_dbc
    estimator, force-enabled. If no CAN master exists, append one."""
    for i, m in enumerate(topology.masters):
        if m.type == "can":
            topology.masters[i] = _make_dbc_master(m.name, dbc_path)
            return
    topology.masters.append(_make_dbc_master("CAN_DBC", dbc_path))


def _inject_named_cans(topology, mappings: list[tuple[str, str]]):
    """Replace each named CAN master with a force-enabled can_dbc estimator."""
    by_name = {m.name: (i, m) for i, m in enumerate(topology.masters)}
    for name, dbc_path in mappings:
        if name not in by_name:
            raise SystemExit(
                f"--can-dbc: CAN master '{name}' not found in topology. "
                f"Available: {', '.join(by_name) or '(none)'}"
            )
        i, m = by_name[name]
        if not m.type.startswith("can"):
            raise SystemExit(
                f"--can-dbc: target '{name}' is type '{m.type}', not a CAN master."
            )
        topology.masters[i] = _make_dbc_master(name, dbc_path)


def _make_dbc_master(name: str, dbc_path: str):
    """Build a can_dbc master. Always enabled=True: the user explicitly provided
    a DBC, so this CAN port is in use regardless of the preset's default."""
    from .schema import Master

    return Master(
        name=name,
        type="can_dbc",
        enabled=True,
        params={"dbc_path": dbc_path, "direction": "both"},
        verify=False,  # explicitly injected by user, not a default
    )


def _parse_can_dbc_arg(values: list[str]) -> list[tuple[str, str]]:
    """Parse repeated --can-dbc NAME=PATH into a list of (name, path) tuples."""
    out = []
    for v in values:
        if "=" not in v:
            raise SystemExit(
                f"--can-dbc expects NAME=PATH, got: '{v}'"
            )
        name, path = v.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise SystemExit(f"--can-dbc: empty name or path in '{v}'")
        out.append((name, path))
    return out


def _write_output(report_text: str, out_path: str | None, fmt: str, report_dict=None):
    if out_path:
        Path(out_path).write_text(report_text, encoding="utf-8")
        return
    # default to stdout
    print(report_text)


def cmd_predict(args) -> int:
    console = Console(highlight=False, no_color=args.no_color)

    can_dbc_mappings = _parse_can_dbc_arg(args.can_dbc) if args.can_dbc else None

    if args.dbc and not args.soc and not args.topology:
        # CAN health report only
        try:
            report = build_health_report(args.dbc, bitrate_kbps=args.can_bitrate)
        except FileNotFoundError:
            print(f"DBC file not found: {args.dbc}", file=sys.stderr)
            return 2
        except Exception as e:
            print(f"Failed to parse DBC: {e}", file=sys.stderr)
            return 2
        if args.format == "json":
            print(dump_json(report.to_dict()))
        elif args.format == "yaml":
            print(dump_yaml(report.to_dict()))
        else:
            render_health_terminal(report, console=console, use_color=not args.no_color)
        if args.output:
            ext = Path(args.output).suffix.lower()
            content = (
                dump_json(report.to_dict()) if ext == ".json" else dump_yaml(report.to_dict())
            )
            Path(args.output).write_text(content, encoding="utf-8")
        return 0

    if args.soc:
        topology = _load_soc(args.soc, args.dbc, can_dbc_mappings)
    elif args.topology:
        topology = load_topology(args.topology)
        _apply_dbcs(topology, args.dbc, can_dbc_mappings)
    else:
        print("Specify one of: --dbc / --soc / -t / --can-dbc", file=sys.stderr)
        return 2

    try:
        prediction = predict(topology)
    except ValueError as e:
        print(f"Prediction error: {e}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(dump_json(build_structured(prediction)))
    elif args.format == "yaml":
        print(dump_yaml(build_structured(prediction)))
    else:
        render_terminal(prediction, console=console, use_color=not args.no_color)

    if args.output:
        ext = Path(args.output).suffix.lower()
        rep = build_structured(prediction)
        content = dump_json(rep) if ext == ".json" else dump_yaml(rep)
        Path(args.output).write_text(content, encoding="utf-8")

    # exit code: non-zero on CRITICAL for CI integration
    margins = evaluate_margin(prediction)
    if any(m.verdict == "CRITICAL" for m in margins):
        return 3
    return 0


def cmd_lint(args) -> int:
    topology = load_topology(args.topology)
    issues = lint(topology)
    if not issues:
        print("OK: no issues found.")
        return 0
    has_error = False
    for iss in issues:
        level = iss.level.upper()
        if iss.level == "error":
            has_error = True
        print(f"[{level}] {iss.rule}: {iss.message}")
    return 1 if has_error else 0


def cmd_list(args) -> int:
    from .estimators.registry import list_estimators

    if args.what == "estimators":
        print("Estimators:")
        for e in list_estimators():
            print(f"  - {e}")
    else:
        print("SoC presets:")
        for p in _list_presets():
            print(f"  - {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="buseval", description="SoC bandwidth evaluation tool.")
    p.add_argument("--version", action="version", version=f"buseval {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("predict", help="Predict DDR bandwidth from a topology / preset / DBC.")
    src = pp.add_mutually_exclusive_group()
    src.add_argument("-t", "--topology", help="Path to topology YAML.")
    src.add_argument("--soc", help="SoC preset name (e.g. rk3588).")
    pp.add_argument("--dbc", help="DBC file path (CAN health report, or inject into first CAN slot of --soc/-t).")
    pp.add_argument(
        "--can-dbc",
        action="append",
        metavar="NAME=PATH",
        help="Inject a DBC into a specific CAN master slot (repeatable). "
             "Example: --can-dbc CAN0=powertrain.dbc --can-dbc CAN2=chassis.dbc",
    )
    pp.add_argument("--can-bitrate", type=float, help="Override CAN bitrate (kbps) for DBC mode.")
    pp.add_argument("-o", "--output", help="Write report to file (json/yaml by extension).")
    pp.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
        default="table",
        help="Output format (default: table).",
    )
    pp.add_argument("--no-color", action="store_true", help="Disable color output.")
    pp.set_defaults(func=cmd_predict)

    lp = sub.add_parser("lint", help="Check topology for missing items / contradictions.")
    lp.add_argument("-t", "--topology", required=True, help="Path to topology YAML.")
    lp.set_defaults(func=cmd_lint)

    lp2 = sub.add_parser("list", help="List available estimators or SoC presets.")
    lp2.add_argument("what", choices=["estimators", "presets"])
    lp2.set_defaults(func=cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as e:
        # SystemExit may carry a string (printed by helpers) — print and map to non-zero
        if e.code and isinstance(e.code, str):
            print(e.code, file=sys.stderr)
        return int(e.code) if isinstance(e.code, int) else 1


if __name__ == "__main__":
    sys.exit(main())
