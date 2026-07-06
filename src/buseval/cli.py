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


def _load_soc(name: str, dbc_path: str | None):
    p = PRESETS_DIR / f"{name}.yaml"
    if not p.exists():
        raise SystemExit(
            f"Unknown SoC preset '{name}'. Available: {', '.join(_list_presets())}"
        )
    topo = load_topology(p)
    if dbc_path:
        _inject_dbc(topo, dbc_path)
    return topo


def _inject_dbc(topology, dbc_path: str):
    """Replace the first CAN master with a can_dbc estimator pointing at dbc_path."""
    from .schema import Master

    for i, m in enumerate(topology.masters):
        if m.type == "can" and m.enabled:
            topo_master = Master(
                name=m.name,
                type="can_dbc",
                enabled=True,
                params={"dbc_path": dbc_path, "direction": "both"},
                verify=True,
            )
            topology.masters[i] = topo_master
            return
    # No CAN slot found — append
    topology.masters.append(
        Master(
            name="CAN_DBC",
            type="can_dbc",
            enabled=True,
            params={"dbc_path": dbc_path, "direction": "both"},
            verify=True,
        )
    )


def _write_output(report_text: str, out_path: str | None, fmt: str, report_dict=None):
    if out_path:
        Path(out_path).write_text(report_text, encoding="utf-8")
        return
    # default to stdout
    print(report_text)


def cmd_predict(args) -> int:
    console = Console(highlight=False, no_color=args.no_color)

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
        topology = _load_soc(args.soc, args.dbc)
    elif args.topology:
        topology = load_topology(args.topology)
    else:
        print("Specify one of: --dbc / --soc / -t", file=sys.stderr)
        return 2

    prediction = predict(topology)

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
    pp.add_argument("--dbc", help="DBC file path (CAN health report, or inject into --soc/-t).")
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
