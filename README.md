[English](README.md) | [中文](README_zh.md)

# buseval — SoC Bandwidth Evaluation Tool

Predict and measure whether DDR bandwidth is sufficient on multi-core heterogeneous SoCs, covering peripherals (CAN / SPI / MIPI / USB / ETH / FLASH) and internal pipelines (ISP / NPU / GPU / Display), and compare predicted vs. measured values.

## Why

Modern SoCs are multi-core heterogeneous systems with many peripherals and accelerators. Bandwidth is often estimated by gut feel in early project stages, and DDR insufficiency is only discovered during measurement — when rework is expensive. buseval turns "early prediction" and "measurement comparison" into a repeatable, auditable engineering workflow.

## Overall Goal

1. **Early prediction** — Input usage-scenario parameters (DBC, resolution/fps, bitrate/load, TOPS, …). The engine estimates per-master / per-pipeline read & write bandwidth, aggregates them, compares against available DDR bandwidth, and reports headroom and alerts.
2. **Measurement collection** — Read real bandwidth from the chip via `perf` / `ddr-perf`.
3. **Prediction vs. measurement** — Quantify deviation, attribute it to parameters or formulas, and iterate to calibrate.

## Current Target (Phase 1)

Only the **early prediction** loop is delivered. No collection or comparison yet. Specifically:

- Pluggable estimator engine with 11 built-in peripheral / pipeline estimators
- Three entry points: direct DBC read (CAN health report), SoC preset, YAML menu
- 7 mainstream SoC presets
- A fake sample DBC + a full-menu YAML template
- Reports: Top-N contributors, read/write separation, assumptions audit, one-line breakdown annotation
- CAN health report (load ratio / Top messages / worst-case frame latency / overload suggestions)
- `lint` for missing-item / contradiction checks

## Quick Start

```bash
pip install -e .

# 1. Try a sample (zero config)
buseval predict --soc rk3588
buseval predict --dbc examples/sample.dbc --can-bitrate 2000

# 2. DBC + preset combined (full evaluation)
buseval predict --soc tda4vh --dbc examples/sample.dbc

# 3. Multi-CAN: route different DBCs to specific CAN controllers
buseval predict --soc tda4vh \
    --can-dbc CAN0=examples/sample.dbc \
    --can-dbc CAN2=examples/sample_heavy.dbc

# 4. Configure your own YAML
cp examples/full_menu.yaml my.yaml
buseval lint my.yaml
buseval predict -t my.yaml
```

## Roadmap

- **Phase 1** — Early prediction loop (current)
- **Phase 2** — Measurement collection (`perf` / `ddr-perf`)
- **Phase 3** — Prediction vs. measurement comparison + attribution chain + `scenario diff`
- **Phase 4** — Coefficient self-calibration + Web UI

## Supported Estimators

CAN (DBC) / CAN (load) / SPI / MIPI CSI / MIPI DSI / USB / ETH / FLASH (NAND / eMMC / UFS) / ISP / NPU / GPU / Display

MIPI CSI / DSI support a `count` parameter for multi-stream multiplexing on one port
(MIPI virtual channels VC0-3, or deserializer aggregation). `count: 4` models 4 cameras
on one CSI port for worst-case bandwidth evaluation; lane capacity is checked against
the aggregate. Defaults to 1 (backward compatible).

## Supported SoC Presets

TI TDA4VH / NVIDIA Orin NX / Horizon J5 / Qualcomm SA8155 / Rockchip RK3588 / Allwinner T527 / NXP S32G

## Documentation

- Design doc: [design.md](design.md)
- Estimation coefficients: `src/buseval/estimators/_coefficients.yaml`
- Changelog: `CHANGELOG.md`

## License

See [LICENSE](LICENSE).
