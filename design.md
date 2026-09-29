# buseval 设计文档

## 1. 设计目标

### 1.1 总目标
建立 SoC 带宽评估的工程化闭环：
- **预测**：从使用场景参数估算各 master 读写带宽，汇总对比 DDR 可用带宽。
- **采集**：从芯片实测计数器读取真实带宽。
- **对比**：量化预测与实测偏差，归因到参数/公式，迭代校准。

### 1.2 Phase 1（已冻结）
前期预测，含命令行和画布。采集与对比不在这次冻结里。

## 2. 核心设计原则

1. **填参数不填答案**：YAML 填使用场景（DBC、分辨率/fps），引擎算带宽。
2. **可插拔 estimator**：每类外设一个估算器，registry 注册，用户可扩展。
3. **分层入口**：DBC 直读（零门槛）→ SoC 预设（一条命令）→ YAML 菜单（满血可配）。
4. **可审计**：激进假设进 assumptions，带 RED/YEL/INFO。没有记录时终端不打印这一段。每项带 breakdown 和 dominant_factor。
5. **占用率裁决**：读写需求分别列出。占用率是两者之和除以可用带宽，绿黄红和裁决都看这个数。
6. **参数与代码分离**：估算系数集中在 `_coefficients.yaml`，可校准不动代码。
7. **数据流显式连线**：pipeline 通过 `source` 字段声明输入来源（master 或 pipeline），支持 p2p 链式 + 多源。

## 3. 系统架构

```
┌──────────────────────────────────────────────────┐
│  CLI (cli.py)                                     │
│  predict / lint / list                            │
├──────────────────────────────────────────────────┤
│  入口层                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ DBC 直读 │ │ SoC 预设 │ │ YAML 菜单(loader)│ │
│  │ --can-dbc│ │ --soc    │ │ -t my.yaml       │ │
│  └────┬─────┘ └────┬─────┘ └────────┬─────────┘ │
│       └─────────────┴────────────────┘           │
│                    ▼                              │
├──────────────────────────────────────────────────┤
│  核心引擎                                         │
│  ┌─────────────────────────────────────────────┐ │
│  │ estimator registry (14 builtins)            │ │
│  │  CAN/SPI/MIPI/USB/ETH/FLASH/ISP/NPU/GPU     │ │
│  │  /Display/VENC/VDEC                         │ │
│  └─────────────────────────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐               │
│  │ predictor    │ │ margin       │               │
│  │ (拓扑排序    │ │ (效率+告警   │               │
│  │  + p2p 链式) │ │  + DDR打满)  │               │
│  └──────────────┘ └──────────────┘               │
├──────────────────────────────────────────────────┤
│  报告层                                           │
│  ┌──────────────┐ ┌──────────────┐               │
│  │ terminal     │ │ structured   │               │
│  │ (rich 表格   │ │ (JSON/YAML   │               │
│  │  + Lv 分级   │ │  +topo_hash) │               │
│  │  +源色一致)  │ │              │               │
│  └──────────────┘ └──────────────┘               │
└──────────────────────────────────────────────────┘

Phase 2/3 扩展点：
- collect 层（perf/ddr-perf）接入 predictor 上游
- compare 引擎接入 predictor 下游

画布在 `gui/`，评估调用同一套 `predict`，不经过 CLI。
```

## 4. 数据模型 (schema.py)

```python
class Master(BaseModel):
    name: str
    type: str                    # "can","spi","mipi_csi"...
    enabled: bool = True         # CAN 默认 false（6 款预设），用 --can-dbc 启用
    params: dict                 # 透传给对应 estimator

class PipelineStage(BaseModel):
    name: str
    read_factor: float          # 多读几遍，不表示格式变化
    write_factor: float
    format: str | None = None   # 本级输出格式；空则沿用输入格式
    width: int | None = None    # 本级输出宽高；空则沿用输入尺寸
    height: int | None = None

class Pipeline(BaseModel):
    name: str
    type: str                    # "isp","npu","gpu","venc","vdec","display"
    mode: Literal["serial","parallel"]
    enabled: bool = True
    source: Optional[Union[str, list[str]]]  # master 名 / pipeline 名 / 列表 / null
    params: dict
    stages: list[PipelineStage]

class DDRChannel(BaseModel):
    name: str
    controller_mt_s: float | None = None
    controller_width_bits: int | None = None
    controller_groups: int = 1
    controller_type: str | None = None
    module_mt_s: float | None = None
    module_width_bits: int | None = None
    module_groups: int = 1
    module_type: str | None = None
    efficiency: float = 0.7
    read_write_ratio: float | None = None  # 旧文件可能还带着，评估不再使用

class IspVpac(BaseModel):
    name: str          # 与 ISP 节点同名，例如 ISP0、ISP1
    mpix_s: float      # 这一路每秒百万像素。TDA4VH 的 ISP0、ISP1 各 600

class Topology(BaseModel):
    masters: list[Master]
    pipelines: list[Pipeline]
    ddr_channels: list[DDRChannel]
    alert_thresholds: dict = {"yellow": 0.6, "red": 0.8}
    ui_lang: str | None = None     # 旧文件里可能还有。界面语言不写在拓扑里
    isp_vpacs: list[IspVpac] = []  # 芯片上的 ISP。节点像素只和同名的那一路比

class BandwidthEstimate(BaseModel):
    read_bw_mbps: float
    write_bw_mbps: float
    breakdown: dict
    dominant_factor: str
    assumptions: list[str] = []
```

## 5. Estimator Registry

```python
class Estimator(Protocol):
    type: str
    def estimate(self, params: dict) -> BandwidthEstimate: ...

def register(type_name: str): ...
def get_estimator(type_name: str) -> Estimator: ...
```

- 内置 14 个估算器，import 时自动注册
- 用户自定义：`@register("my_ip")`
- 系数集中在 `estimators/_coefficients.yaml`

## 6. 估算公式表

| type | 输入 | 公式 |
|---|---|---|
| can (报文文件) | dbc_path、标准、码率 | Σ(DLC×8 / cycle_time) bit/s ×1.3(帧开销) → MB/s。一份文件是一路，没有 bus id |
| can (通用) | 标准、码率、load_pct | 数据相码率 × 负载 × 0.7(有效载荷) → MB/s。经典 CAN 最大 1 Mbps，CAN FD 仲裁最大 1 Mbps、数据相最大 8 Mbps |
| spi | clock_mhz, xfer_bytes, xfer_hz | min(clock×1e6/8, xfer_bytes×xfer_hz) |
| mipi_csi | w,h,fps,bpp,lanes,count | per_stream=w×h×fps×bpp/8；aggregate=per×count；校验 aggregate vs lane 上限 |
| mipi_dsi | w,h,fps,bpp,lanes,count | 同上（DSI 读 DDR，CSI 写 DDR） |
| usb | version, util_pct | nominal(480/5000/10000 Mbps) × util × 0.9 |
| eth | link_gbps, util_pct, mtu | link×util×mtu/(mtu+38) |
| flash | type, seq_r, seq_w, util, random_ratio | seq×util×[(1-r)+0.3r] |
| isp | w,h,fps,format,stages[] | 一个 ISP 节点是一路画面，不乘 count。每级输入帧×read_factor、输出帧×write_factor；输出帧用本级格式和尺寸；serial 取 max，parallel 取 sum；交给下游的是最后一级画面。像素只和同名的芯片 ISP（ISP0、ISP1）比较 |
| npu | params_mb, act_mb, inference_fps, tops_peak, sources[] | weight=params×fps；act=act_mb×2×fps；input=Σ各源(w×h×src_fps×bpp×count/8 或上游画面)；read=weight+act/2+input |
| gpu | w,h,fps,bpp,overdraw | w×h×fps×bpp×overdraw/8 |
| display | w,h,fps,format 或 source_input_mbps | w×h×fps×bpp/8（有上游时用上游画面） |
| venc | w,h,fps,format,codec；pipeline 上游另给 source_input_mbps | read=上游画面（没有上游时用本级画面）；write=本级画面/compression_ratio |
| vdec | w,h,fps,format,codec | write=本级画面；read=上游画面，没有上游时为本级画面/ratio |

### codec 压缩比（VENC/VDEC）
`codec` 参数选默认压缩比（_coefficients.yaml 可配）：
- h264: 30
- h265: 50
- av1: 70
- 单条覆盖：`params.compression_ratio: 40`

### 像素格式
各模块共用 `src/buseval/estimators/formats.py`。NV12、NV21、I420、YV12 与 YUV420 都是 12 位；YUYV、UYVY、NV16 与 YUV422 都是 16 位；RGB565 是 16 位；BGR888 是 24 位；BGRA8888、ARGB8888 是 32 位。P010 是 10 位装在 16 位里的 4:2:0，按 24 位计。`custom` 用手填的 bpp。

### 界面字段
模块位置 `ui_x`、`ui_y` 写在同一份 YAML 里。界面语言记在本机，打开项目不改语言。DDR 通道也可以带 `ui_x`、`ui_y`。CLI 预测不读 `ui_` 开头的键，拓扑哈希也会去掉它们。打开拓扑或预设不自动评估。保存前要过校验：有错误，或预测算不下去，就不写文件。警告不挡住保存。评估结果不写回文件。

画布上的 DDR 节点就是拓扑里已有的那一路通道，不是模块库里的新类型。没有打开工程时不画它。打开之后，卡片上只写可用带宽的数字，例如 35,840 MB/s；速率和位宽在双击打开的对话框里。顶栏只显示占用，双击不打开配置。网口、USB、CAN、SPI、存储，以及 GPU、VDEC，放下就用一条本色虚线连到 DDR。线的两头各有一个点，都可以沿卡片的边拖。CSI、ISP、VENC、DISP、NPU 不另拉虚线，画面入写成「CSI1 via DDR」。DSI 的入不写 via DDR，因为它不再读内存。画面线仍是实线贝塞尔，用源头模块自己的颜色，画在卡片下面。评估后模块按不同占比落在一条连续色档上，从品红经紫、蓝紫、天蓝淡到接近白；占比相同落在同一处。DDR 卡片和顶栏按占用率上色，不到黄阈值是绿，到了是黄，到了红阈值是红。评估后卡片右侧用大字居中写出这个占用率。

VENC 可以连到 ETH。连上之后，ETH 的 DDR 读等于上游画面或码流，不再用链路速率 × 利用率另加一笔。链路装不下这条流时记一条红色假设。VENC 自己的写和 ETH 的读是两次 DMA，两条虚线都在。没有上游的 USB、CAN、ETH 仍按各自的利用率估算。

## 7. Predictor 算法（含 p2p 拓扑排序）

1. 先算不吃 pipeline 上游的 enabled master
2. 对 pipeline 做拓扑排序（DFS）：被 source 的 pipeline 先算
3. 环检测：A→B→A 报错 `cyclic pipeline dependency`
4. 每个 pipeline：
   - master source → 继承图像尺寸（w/h/fps/bpp，ISP 不继承 count），下游自算帧流
   - pipeline source → 非 DSI 拿上游画面（`output_mbps`，没有则 `write_bw`）；DSI 拿上游读带宽，自己的 DDR 为 0
   - 多源 [CSI0, ISP0] → master 贡献尺寸 + pipeline 贡献画面，分别处理后累加
5. 接到 pipeline 上的 ETH：DDR 读 = 那条上游画面或码流，不再叠加 link × util
6. 全局汇总：R_demand = Σ read，W_demand = Σ write
7. 每项 assumptions 合并进全局段（带 level 分级）。不再写「input from」；来源已经画在画布上

## 8. Margin 评估

### DDR 有效带宽（木桶效应）
```
controller_peak = controller_mt_s × controller_width_bits / 8 × controller_groups
module_peak     = module_mt_s     × module_width_bits     / 8 × module_groups
effective_peak  = min(controller_peak, module_peak)                  ← 瓶颈取短板
available       = effective_peak × efficiency
```
- MT/s 已包含 DDR 双沿（Double Data Rate），不再乘 2
- 芯片 DDR 控制器上限 vs 外贴颗粒带宽，取最小值
- 两边速率没齐时有效峰值为 0
- bottleneck 标注："controller" / "module" / "matched"

### 占用率与告警
- `R-util = R_demand / available`，`W-util = W_demand / available`
- `occupancy = (R_demand + W_demand) / available`，等于两侧相加
- 三列百分比用同一条线涂色：不到 yellow 绿，到了黄，到了 red 红
- 裁决只看 occupancy：`≥ red(0.8)` → CRITICAL；`≥ yellow(0.6)` → WARN；否则 OK
- DDR 打满告警：`occupancy ≥ 0.8` 进 assumptions 段（RED 级）
- 没有风险记录时，终端不打印 assumptions 段

## 9. Assumptions 审计（分级）

每条 assumption 带 level：
- **RED**：激进 util>0.9 / lane 超 lane 上限 / NPU tops 超限 / DDR occupancy≥0.8
- **YEL**：非典型 stage 系数 / NPU fps < source fps / CAN load>0.7
- **INFO**：估算器仍声明的事实。来源连线画在画布上，不再写进这里

每项合并成一行（避免重复），取最严重 level 作行级 level。终端 Lv 列染色（RED=红 / YEL=黄 / i=暗青）。

## 10. CAN 健康报告（DBC 直读单独模式）

一份 DBC 是一路 CAN，不按文件里的总线名拆开。标准二选一：经典 CAN（ISO 11898，码率最大 1000 kbps，报文最长 8 字节），或 CAN FD（仲裁最大 1000 kbps，数据相最大 8000 kbps，报文长度只允许 0–8、12、16、20、24、32、48、64）。负载按数据相码率（经典 CAN 就是那一档码率）去除报文比特。报文长度不合当前标准时不算负载。界面先写负载和最坏延迟，这句按不到 60% 绿、到 60% 黄、到 80% 红上色。下面五行演算用默认字色。括号用数据码率或码率，与上面的输入框一致。负载和最坏延迟写在各自等号前面。每一行有一句提示。双击画布上的 CAN 打开这一页，报文文件和通用二选一，确定后写回该节点。菜单里打开同一页只查看文件，不写回。

## 10.5. GMSL 链路带宽（独立工具）

独立于 SoC topology，计算 GMSL 串行链路所需带宽。

### 公式
同轴（pixel mode）和 CSI 口分开算。

```
PCLK     = 宽 × 高 × fps ×（heartbeat 开 ? blanking : 1）
bpp_link = max(bpp, 9)
link_bw  = PCLK × (bpp_link + crc) × encoding × (2048/2047) × fec
csi_bw   = 宽 × 高 × fps × blanking × bpp
```

- blanking 默认 1.2。CSI 始终乘它。同轴只在 heartbeat 打开时乘它。摄像头进 CSI 解串器时 heartbeat 默认关。
- RAW8 / EMB8 的同轴 bpp 先抬到 9。CSI 仍用真实 bpp。
- 像素 CRC 默认开，+0.5 bpp。
- 编码默认 9b/10b（10/9 = 1.1111）。可选 8b/10b（10/8 = 1.25）或 none（1）。
- 包开销固定 2048/2047。
- FEC 默认关。打开时乘 128/120 = 1.0667。GMSL3 始终乘这一项；FEC 已经打开时不再乘第二次。
- 线速率在 `_coefficients.yaml` 的 `gmsl.link_tiers`。占用率 = 该档使用的链路带宽 / 线速率。

### GMSL 链路等级
| 档 | 线速率 |
|---|---|
| GMSL1 | 3.12 Gbps |
| GMSL2 3G | 3 Gbps |
| GMSL2 6G | 6 Gbps |
| GMSL3 | 12 Gbps |

四个色框按不到 0.6 绿、到 0.6 黄、到 0.8 红上色。装得下的最低档标推荐。界面只有一页：1 到 4 路共用一根同轴，同轴带宽是各路之和。只有 1 路时展开完整算式，多路时用乘积和合计。命令行用青色边框包住同一段文字，不打印 CSI 配置。

CSI 配置用各路消隐后像素率之和。D-PHY lane 为 1–4，每 lane 速率再除以 2 得到时钟脚。C-PHY trio 为 1–3，符号率 = 每 trio 比特率 / 2.28。配置值按 0.1 Gbps 向上取整。超过 1.5 Gbps/lane 时提示 deskew。

单路不落盘。多路 YAML 写入 blanking、heartbeat、encoding、fec、pixel_crc、phy、lanes，以及每路 name、width、height、fps、format。custom 才写 bpp。旧文件里的 encoding_factor / overhead_factor 不再参与计算。

### CLI
```bash
# 单路（参数串，空格分隔 key=value）
buseval predict --GMSL width=1920 height=1080 fps=30 bpp=12
buseval predict --GMSL width=1920 height=1080 fps=30 bpp=12 blanking=1.25

# 多路（YAML）
buseval predict --GMSL examples/gmsl_links.yaml
```
- 含 `=` → 参数串；不含 `=` → YAML 文件（自动区分）
- blanking 是全局的（YAML 顶部 `blanking: 1.25` 覆盖所有路，不每路重复）

## 11. SoC 预设

`presets/<chip>.yaml` 含该芯片的：
- 典型外设清单（CAN 路数、MIPI 通道、USB 版本、ETH 速率、FLASH 类型）
- ISP 级数及默认系数 + source 连线（CSI1→ISP0）
- NPU TOPS / 典型模型参数 + source 连线（[CSI0, ISP0]）
- VENC/VDEC + source 连线（ISP0→VENC0）
- GPU/Display + source 连线（ISP0→DISP0）
- DDR 类型/速率/通道数 → 理论峰值
- **CAN 默认 enabled: false**（6 款非网关预设）；s32g（网关）保持启用

7 款：tda4vh / orin_nx / j5 / sa8155 / rk3588 / t527 / s32g

### 预设默认数据流（tda4vh 示例）
```
CSI0(4-cam) ────────────────────────→ NPU0 (raw 域 AI)
CSI1 ──→ ISP0 ──┬──→ NPU0 (YUV 推理)   [多源混合: CSI0+ISP0]
                ├──→ VENC0 (h265 录像)
                └──→ DISP0 (低延迟显示)
VDEC0 (独立回放, h265)
```

## 12. Lint 规则

`lint_rules.yaml`，可扩展：
- 类别全缺：无 Display 且无 NPU 且无 GPU → 警告
- 拓扑矛盾：有 CSI 无 ISP / 有 NPU 无 weight 来源 → 警告
- 必填缺失：无 DDR 通道 → 报错
- 参数越界：load_pct∈[0,1]、lanes∈{1,2,3,4} → 报错
- ISP 多源：ISP source 是 list 且 len>1 → 报错
- 环依赖：pipeline source 形成环 → 报错
- NPU fps < source：每源分别 warning（async; not capped）
- source 引用不存在 → 报错
- source 引用的 pipeline 禁用 → 警告

## 13. 报告格式

### 13.1 终端（rich 表格）
```
DDR Bandwidth Report
══════════════════════════════════════════════════
DDR0  available 35840
  R-demand 6340 MB/s   R-util 17.7%
  W-demand 3761 MB/s   W-util 10.5%
  Occupancy 28.2%  [OK]
  三个百分比按不到 0.6 绿、到 0.6 黄、到 0.8 红上色

Top contributors (read+write):
  1. NPU0        4300 MB/s  20.5%  [from CSI0+ISP0]   ← source 名染色
  2. ISP0        3740 MB/s  17.8%  [from CSI1]
  ...

Assumptions (verify before trusting):
  Lv   Item    Message
  RED  NPU0    aggressive util_pct=0.95
  RED  DDR0    occupancy 96.4% >= 80% (DDR near full)
  YEL  CSI0    uses unverified default value
```

### 13.2 结构化 (report.yaml / report.json)
含时间戳、**topology_hash**（拓扑结构哈希，同拓扑两次预测哈希一致）、逐项 estimate（含 breakdown）、汇总、assumptions（带 level）、verdict。

## 14. CLI

```
buseval predict --dbc f.dbc                    # CAN 健康报告
buseval predict --dbc f.dbc --can-bitrate 2000 # CAN-FD 2Mbps
buseval predict --soc <chip>                   # 预设评估
buseval predict --soc <chip> --dbc f.dbc       # DBC 注入第一个 CAN 槽
buseval predict --soc <chip> \
    --can-dbc CAN0=a.dbc --can-dbc CAN2=b.dbc  # 多 CAN 通路分别挂 DBC
buseval predict --GMSL width=1920 height=1080 fps=30 bpp=12  # GMSL 单路
buseval predict --GMSL examples/gmsl_links.yaml              # GMSL 多路
buseval predict -t my.yaml                     # 自配
buseval lint -t my.yaml
buseval list presets                           # 列预设
buseval list estimators                        # 列估算器
```

公共参数：`-o/--output report.yaml` `--format {table,json,yaml}` `--no-color`

## 15. 目录结构

```
src/buseval/
├── __init__.py
├── cli.py
├── schema.py
├── loader.py
├── lint.py
├── estimators/
│   ├── __init__.py
│   ├── registry.py
│   ├── _coefficients.yaml
│   └── builtins/
│       ├── can_dbc.py
│       ├── can_load.py
│       ├── spi.py
│       ├── mipi.py
│       ├── usb.py
│       ├── eth.py
│       ├── flash.py
│       ├── isp.py
│       ├── npu.py
│       ├── gpu_display.py
│       └── venc_vdec.py
├── engine/
│   ├── predictor.py        # 含拓扑排序 + 环检测 + p2p
│   └── margin.py
├── dbc/
│   ├── parser.py
│   └── health_report.py
├── gmsl/
│   ├── calculator.py       # GMSL 链路带宽公式 + 推荐表
│   └── report.py           # GMSL 终端 + 结构化报告
├── presets/
│   ├── tda4vh.yaml
│   ├── orin_nx.yaml
│   ├── j5.yaml
│   ├── sa8155.yaml
│   ├── rk3588.yaml
│   ├── t527.yaml
│   └── s32g.yaml
└── report/
    ├── terminal.py         # 含 Lv 分级 + 源色一致
    └── structured.py       # 含 topology_hash
examples/
├── sample.dbc              # classic CAN，10 条小报文
├── sample_heavy.dbc        # CAN-FD，17 条 64 字节大帧
├── gmsl_links.yaml         # GMSL 多路配置示例
└── full_menu.yaml          # 完整菜单模板
tests/
└── ...
```

## 16. 依赖

`pydantic>=2` `pyyaml` `rich` `cantools` `pytest`。图形界面额外需要 `PySide6`（`pip install -e '.[gui]'`）。

## 17. 路线图

CLI 与 GUI 互不调用，交集只有拓扑 YAML。`buseval predict -t` 是 Phase 1 已有命令。

- **Phase 1（已冻结）**：PySide6 画布。菜单打开预设或 YAML，拖入模块，连线即 `source`，双击改参数，菜单「评估」在进程内调用 `predict`，模块在绿黄红以外按档变淡，DDR 按占用率显示绿、黄、红。
- **阶段 A**：`buseval collect --platform arm_pc` 走 `collectors/arm_pmu.sh`（`perf stat`），写出 `meas.json`。`buseval compare -t topo.yaml -m meas.json` 与 GUI「导入实测」各自对比，不交换结果。
- **阶段 B**：SoC DDR。平台清单再登记一个适配器；计数器已是命令或 sysfs 时用 shell，只有必须映射寄存器时才写 C。
- **阶段 C**：板端 CAN（SocketCAN）与 GMSL（解串器），`kind` 分别为 `can_bus`、`gmsl_link`，文件格式不变。
- 系数自校准不在本轮。

会话层在 `session.py`：载入、保存、评估（预测 + 余量只算一次）。连线与环检测在 `engine/graph.py`。帧流字节率在 `estimators/frame.py`。假设等级由估算器直接给出。

## 18. 开放问题（Phase 1 冻结后仍留着）

- ISP 各 stage 系数是否需要按厂商校准？
- NPU 估算用 TOPS 还是参数量为主？（当前两者都支持，tops 仅作 sanity check）
- 7 款 SoC 预设的真实参数需团队成员校准（当前用公开规格推算）
- NPU 逐层 `layers[]` 精确建模（当前聚合 params+act，后续可扩展）
