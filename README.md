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

CAN (DBC) / CAN (load) / SPI / MIPI CSI / MIPI DSI / USB / ETH / FLASH (NAND / eMMC / UFS) / ISP / NPU / GPU / Display / VENC (H.264/H.265/AV1) / VDEC

MIPI CSI / DSI support a `count` parameter for multi-stream multiplexing on one port
(MIPI virtual channels VC0-3, or deserializer aggregation). `count: 4` models 4 cameras
on one CSI port for worst-case bandwidth evaluation; lane capacity is checked against
the aggregate. Defaults to 1 (backward compatible).

## Pipeline Wiring (`source`) & ISP Stages

Pipelines (ISP / NPU / VENC / VDEC / Display) can declare an optional `source`
field naming a master (e.g. `CSI1`) **or another pipeline** (e.g. `ISP0`) whose
output they consume. This wires the data flow explicitly (pipeline-to-pipeline
chaining supported via topological sort; cycles are rejected).

- **master source** (e.g. `CSI0`): the pipeline inherits the master's image
  dimensions (width/height/fps/bpp/count) and computes its own frame stream.
- **pipeline source** (e.g. `ISP0`): the pipeline receives the upstream
  pipeline's **output bandwidth** (write_bw) as its input — useful for
  `ISP0→NPU0` (NPU reads ISP's YUV output), `ISP0→VENC0` (encode ISP output),
  `ISP0→DISP0` (low-latency viewfinder path).
- IPs that read directly from DDR (no source) leave `source: null` and put
  width/height/fps/bpp directly in `params`.

```yaml
pipelines:
  - name: ISP0
    type: isp
    source: CSI1              # master → pipeline (inherit CSI1's dimensions)
    mode: serial              # serial = max of stages; parallel = sum
    stages:                   # fully customisable — name & factors are arbitrary
      - {name: bayer,     read_factor: 1.0, write_factor: 1.0}
      - {name: demosaic,  read_factor: 1.5, write_factor: 2.0}
      - {name: yuv_scale, read_factor: 2.0, write_factor: 1.0}
      # vendor-specific stages — any name, any factor:
      - {name: custom_NR,  read_factor: 1.8, write_factor: 1.2}
      - {name: WDR,        read_factor: 2.5, write_factor: 1.5}
    # each stage's DDR traffic = frame_stream × factor
  - name: VENC0
    type: venc                # encode ISP output for recording; codec = h264|h265|av1
    source: ISP0              # p2p: VENC reads ISP0's YUV output
    params: {width: 1280, height: 720, fps: 60, bpp: 16, codec: h265}
  - name: VDEC0
    type: vdec                # playback decoder (independent; no source)
    params: {width: 1920, height: 1080, fps: 30, bpp: 16, codec: h265}
  - name: NPU0
    type: npu
    source: [CSI0, ISP0]      # multi-source: CSI0 raw-domain (4-cam) + ISP0 YUV output (p2p)
                              #   each source uses its NATIVE fps (no sync/cap),
                              #   input is the SUM of per-source MB/s (not fps),
                              #   weight + activation are computed once (shared model)
    params: {params_mbytes: 80, activation_mbytes: 40, inference_fps: 30, tops_peak: 8}
  - name: DISP0
    type: display
    source: ISP0              # p2p: Display reads ISP0's YUV (low-latency path)
```

All SoC presets ship with a default wiring (CSI1→ISP0→{NPU0, VENC0, DISP0}; NPU0
also sources CSI0) as a starting point; edit the YAML to match your board.

### Codec compression ratios (VENC / VDEC)

`codec` selects a default compression ratio (configurable in
`_coefficients.yaml`): h264=30, h265=50, av1=70. Override per instance with
`params.compression_ratio: 40`.

## Supported SoC Presets

TI TDA4VH / NVIDIA Orin NX / Horizon J5 / Qualcomm SA8155 / Rockchip RK3588 / Allwinner T527 / NXP S32G

## Documentation

- Design doc: [design.md](design.md)
- Estimation coefficients: `src/buseval/estimators/_coefficients.yaml`
- Changelog: `CHANGELOG.md`

## License

See [LICENSE](LICENSE).
