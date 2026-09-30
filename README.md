[English](README.md) | [中文](README_zh.md)

# buseval — SoC Bandwidth Evaluation Tool

At approval, choose the SoC and the DDR from the functions. After engineering samples return, whole-machine read and write totals check the deviation between the summed estimate and the measurement.

## Why

Approval starts by fixing the functions. Once the functions are fixed, there is a basis for the SoC, and for the DDR capacity on the board. That is the first selection.

After the first selection, the same functions go back into the tool for repeated simulation. Change the SoC variant, change the DDR supplier, compare performance and price for each pair, and keep the pair with the best performance for the price.

Purchasing places the chosen variant and the chosen supplier on the BOM. Finance books the SoC and the DDR as cost per unit. Another variant is another chip cost; another supplier is another unit price for the same capacity. The simulations are how those combinations are compared.

What the engineering samples check is the machine total. A close total can still hide one block estimated high and another low. CAN load and the GMSL link are part selection after approval. They are not part of this SoC and DDR choice, and can be checked by a hardware collection.

## Quick Start

Step 1 is approval: choose the SoC and the DDR capacity from the functions, then simulate variants and suppliers. CAN and GMSL are part selection after approval.

```bash
pip install -e .

# 1. Try a sample (zero config)
buseval predict --soc tda4vh
```

![SoC Bandwidth Report](./gallery/SOC.png)

```bash
# 2. CAN-FD health report (2 Mbps)
buseval predict --dbc examples/sample.dbc --can-bitrate 2000
# Several buses: route each DBC to a CAN controller
buseval predict --soc tda4vh --can-dbc CAN0=examples/sample.dbc --can-dbc CAN2=examples/sample_heavy.dbc
```

![CAN Health Report](./gallery/CAN.png)

```bash
# 3. GMSL link bandwidth
buseval predict --GMSL width=1920 height=1080 fps=30 bpp=12
buseval predict --GMSL examples/gmsl_links.yaml
```

![GMSL Link Bandwidth Report](./gallery/GMSL.png)

```bash
# 4. Configure your own YAML
cp examples/full_menu.yaml my.yaml
buseval predict -t my.yaml
```

## GUI

```bash
pip install -e '.[gui]'
buseval gui
```

Startup asks which page to open. SoC DDR is the canvas: drag modules, connect them, double-click to edit, then evaluate. The DDR card shows occupancy in green, yellow, or red. CAN and GMSL are separate pages.

GMSL is one page: up to four cameras on one coax, the link formula, and a tier. The CLI panel above is the same calculation.

![GMSL link dialog](./gallery/gmsl-gui.png)

## Measurement

`buseval collect` reads the memory controller's read and write counts and prints whole-machine DDR bandwidth. `-o` also writes `meas.json`. There is no platform flag. The counters come from a scan of `perf list` and `/sys/bus/event_source/devices`, keeping a PMU that has both a read burst and a write burst. An aggregate pair is used alone; per-channel beats are summed only when no aggregate exists. CPU cache misses are not used. The total includes DMA and is not split per camera or NIC.

Bytes per count come from this machine: the event's scale, a perf metric expression, or the MB/s this `perf stat` already printed. If none of those exist, the panel has no `count size` and no MB/s. When this command will ask for the administrator password, it prints the risk notice first. A live sudo ticket skips both the notice and the password. Loading `amd_uncore` on AMD, when the counters are not registered yet, asks the same way. A missing counter exits instead of inventing a number.

`src/buseval/embedded/ddr_pmu.sh` is the copy that runs on a board. It does not call buseval. It prints the same count window, count size, DDR read, and DDR write lines, keeping perf's full elapsed time. `-o` saves that text without color. `buseval collect --from` reads that text and uses the byte size on the count size line.

```bash
buseval collect
buseval collect -o meas.json
buseval compare -t my.yaml -m meas.json
```

![DDR collect](./gallery/collect.png)

Compare sets the measured machine total against the sum of the prediction. The GUI imports the same `meas.json` from Measure → Import.

## Roadmap

- **Stage A, in place** — `buseval collect` on this machine reads whole-machine DDR.
- **Stage B** — Copy `ddr_pmu.sh` onto the SoC. The result is still the machine total. It has not been run on a board yet. C is only for a counter that is neither perf nor sysfs.

## Supported Estimators

CAN / SPI / MIPI CSI / MIPI DSI / USB / ETH / FLASH / ISP / NPU / GPU / Display / VENC / VDEC

Cameras sharing one CSI or DSI port add up through `count`. Wiring, ISP stages, and compression ratios are in [design.md](design.md).

## Common SoCs

TI TDA4VH / NVIDIA Orin NX / Horizon J5 / Qualcomm SA8155 / Rockchip RK3588 / Allwinner T527 / NXP S32G

DDR rate and width in the presets come from public specifications.

## Documentation

- Design doc: [design.md](design.md)
- Estimation coefficients: `src/buseval/estimators/_coefficients.yaml`

## License

See [LICENSE](LICENSE).
