[English](README.md) | [中文](README_zh.md)

# buseval — SoC 带宽评估工具

在板子还能改的时候把 DDR 定下来。芯片上有了实测，再把估算拿回去对。

## 为什么需要

位宽、颗粒速率和用几颗，一落板就改不动。带宽不够，先丢帧、再改板。带宽留得太多，每出一台都在为用不上的内存付钱。buseval 用来在设计还开着的时候做这个选择，也用来在芯片能够测量之后再核一次。

## 总目标

1. **定板之前**：把场景写进来，摄像头、DBC、码率、TOPS。引擎把各模块的读写加总，对着这块板实际给得出的 DDR，标出谁在吃带宽。
2. **上板之后**：从芯片上把同一份带宽读回来。
3. **有了实测**：两边摆在一起，看模型偏在哪里，把内存停在够用的那一档。

## Phase 1（已冻结）

前期预测，命令行和画布都在里面。采集和对比留在路线图。

- 14 个内置估算器
- 入口：SoC DDR 拓扑（空白画布、常用芯片或私有芯片）、CAN 健康报告、GMSL 链路
- 7 颗常用芯片、样例 DBC、完整菜单模板
- 报告：贡献者、读写需求、R-util / W-util / 占用率，三条百分比同一套绿黄红。裁决看占用率。没有风险记录时不打印 assumptions
- 画布：拖入模块、连 `source`、进程内评估。DDR 卡片用绿、黄、红写出占用率
- `lint` 查漏项和矛盾

## 快速开始

```bash
pip install -e .

# 1. 看样例（零配置）
buseval predict --soc tda4vh
```

![SoC 带宽报告](./gallery/SOC.png)

```bash
# 2. CAN-FD 健康报告（2 Mbps）
buseval predict --dbc examples/sample.dbc --can-bitrate 2000
# 2b. 多 CAN 通路：把不同 DBC 挂到指定 CAN 控制器
buseval predict --soc tda4vh \
    --can-dbc CAN0=examples/sample.dbc \
    --can-dbc CAN2=examples/sample_heavy.dbc
```

![CAN 健康报告](./gallery/CAN.png)

```bash
# 3. GMSL 链路带宽（独立工具，单路）
buseval predict --GMSL width=1920 height=1080 fps=30 bpp=12
# 3b. GMSL 多路（YAML）
buseval predict --GMSL examples/gmsl_links.yaml
```

![GMSL 链路带宽报告](./gallery/GMSL.png)

```bash
# 4. 自配 YAML
cp examples/full_menu.yaml my.yaml
buseval lint my.yaml
buseval predict -t my.yaml
```

## 图形界面

CLI 和 GUI 互不调用，共用同一份拓扑 YAML。`buseval predict -t` 仍是第一阶段的命令。

```bash
pip install -e '.[gui]'
buseval gui
```

启动时先选要评估的类型。

- **SoC DDR 拓扑**：空白画布、常用芯片，或一份私有芯片文件。画布左侧是模块库。拖入模块，连线写入 `source`，双击节点改参数，双击 DDR 卡片改通道。下方 DDR 条只显示状态。菜单「评估」在界面进程里计算。模块按读写占比变深，DDR 卡片用绿、黄、红写出占用率。保存后的 YAML 仍可用 `buseval predict -t` 再跑。
- **CAN 健康报告**和 **GMSL 链路**从同一个选择窗口打开，也在文件菜单里。它们不进这张拓扑图。

GMSL 是一页：一根同轴上最多四路摄像头、链路公式，以及等级。上面的命令行面板是同一套计算。

![GMSL 链路界面](./gallery/gmsl-gui.png)

## 实测

采集器按平台选择，产出都是 `meas.json`。基础 ARM PC 用 `src/buseval/collectors/arm_pmu.sh` 调 `perf`。板上能用 shell 读计数器就用 shell，不必先写成 C。

```bash
buseval collect --platform arm_pc -o meas.json
buseval compare -t my.yaml -m meas.json
```

GUI 用「实测 → 导入实测」读同一份文件。对比在各自进程里完成，不把 CLI 的输出交给 GUI。

## 路线图

- **Phase 1（已冻结）**：命令行和界面都覆盖 SoC DDR、CAN 健康报告、GMSL 链路。DDR 占用率显示绿、黄、红
- **阶段 A**：基础 ARM PC 上用 `perf` 采集 CPU 内存带宽，和预测对比
- **阶段 B**：SoC DDR 计数器（先 TDA4VH），能用 shell 就不写 C
- **阶段 C**：板端 CAN / GMSL，仍写 `meas.json`
- 系数自校准仍在后面，不在本轮

## 支持的估算器

CAN(DBC) / CAN(load) / SPI / MIPI CSI / MIPI DSI / USB / ETH / FLASH(NAND/eMMC/UFS) / ISP / NPU / GPU / Display / VENC(H.264/H.265/AV1) / VDEC

MIPI CSI / DSI 支持 `count` 参数，建模单端口多路复用（MIPI 虚拟通道 VC0-3，或解串器汇聚）。
`count: 4` 表示一个 CSI 口接 4 路摄像头，做最坏情况带宽评估；lane 容量按聚合带宽检查。
默认 1（向后兼容）。

## Pipeline 连线（`source`）与 ISP stages

pipeline（ISP / NPU / VENC / VDEC / Display）可声明可选的 `source` 字段，指向某个
master（如 `CSI1`）**或另一个 pipeline**（如 `ISP0`）的输出。这样数据流显式可见
（pipeline→pipeline 链式支持，按拓扑排序计算；环依赖会报错）。

- **master 源**（如 `CSI0`）：pipeline 继承 master 的图像尺寸（width/height/fps/bpp/count），
  自己算帧流。
- **pipeline 源**（如 `ISP0`）：pipeline 拿到上游 pipeline 的**输出带宽**（write_bw）
  作输入——用于 `ISP0→NPU0`（NPU 读 ISP 的 YUV 输出）、`ISP0→VENC0`（编码 ISP 输出）、
  `ISP0→DISP0`（低延迟取景器通路）。
- 直接从 DDR 读的 IP（无源）保持 `source: null`，把 width/height/fps/bpp 直接写在
  `params` 里。

```yaml
pipelines:
  - name: ISP0
    type: isp
    source: CSI1              # master → pipeline（继承 CSI1 的尺寸）
    mode: serial              # serial = 各级取 max；parallel = 各级求和
    stages:                   # 完全可自定义 — 名称和系数任意
      - {name: bayer,     read_factor: 1.0, write_factor: 1.0}
      - {name: demosaic,  read_factor: 1.5, write_factor: 2.0}
      - {name: yuv_scale, read_factor: 2.0, write_factor: 1.0}
      # 厂商专有级 — 任意名称、任意系数：
      - {name: custom_NR,  read_factor: 1.8, write_factor: 1.2}
      - {name: WDR,        read_factor: 2.5, write_factor: 1.5}
    # 每级 DDR 流量 = frame_stream × factor
  - name: VENC0
    type: venc                # 编码 ISP 输出录像；codec = h264|h265|av1
    source: ISP0              # p2p：VENC 读 ISP0 的 YUV 输出
    params: {width: 1280, height: 720, fps: 60, bpp: 16, codec: h265}
  - name: VDEC0
    type: vdec                # 回放解码器（独立，无 source）
    params: {width: 1920, height: 1080, fps: 30, bpp: 16, codec: h265}
  - name: NPU0
    type: npu
    source: [CSI0, ISP0]      # 多源：CSI0 raw 域（4 路）+ ISP0 YUV 输出（p2p）
                              #   各源用原生 fps（不同步、不 cap），
                              #   input 是各源 MB/s 之和（不是 fps 之和），
                              #   weight + activation 只算一次（模型共享）
    params: {params_mbytes: 80, activation_mbytes: 40, inference_fps: 30, tops_peak: 8}
  - name: DISP0
    type: display
    source: ISP0              # p2p：Display 读 ISP0 的 YUV（低延迟通路）
```

所有 SoC 预设默认带一条连线（CSI1→ISP0→{NPU0, VENC0, DISP0}；NPU0 同时也 source CSI0）
作为起点，按你的板子在 YAML 里改即可。

### 编码器压缩比（VENC / VDEC）

`codec` 选默认压缩比（可在 `_coefficients.yaml` 配置）：h264=30、h265=50、av1=70。
单条覆盖用 `params.compression_ratio: 40`。

## 常用芯片

TI TDA4VH / NVIDIA Orin NX / 地平线 J5 / 高通 SA8155 / 瑞芯微 RK3588 / 全志 T527 / NXP S32G

## 文档

- 设计文档：[design.md](design.md)
- 估算系数：`src/buseval/estimators/_coefficients.yaml`

## License

见 [LICENSE](LICENSE)。
