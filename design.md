# buseval 设计文档

## 1. 设计目标

### 1.1 总目标
建立 SoC 带宽评估的工程化闭环：
- **预测**：从使用场景参数估算各 master 读写带宽，汇总对比 DDR 可用带宽。
- **采集**：从芯片实测计数器读取真实带宽。
- **对比**：量化预测与实测偏差，归因到参数/公式，迭代校准。

### 1.2 现有目标（Phase 1）
仅实现"前期预测"闭环。不涉及采集与对比。

## 2. 核心设计原则

1. **填参数不填答案**：YAML 填使用场景（DBC、分辨率/fps），引擎算带宽。
2. **可插拔 estimator**：每类外设一个估算器，registry 注册，用户可扩展。
3. **分层入口**：DBC 直读（零门槛）→ SoC 预设（一条命令）→ YAML 菜单（满血可配）。
4. **可审计**：所有默认值/激进假设进 assumptions 段分级标色（RED/YEL/INFO），预测结果带 breakdown + dominant_factor。
5. **峰值汇总 + 读写分离**：master 峰值带宽加总对比 DDR；读写分别评估。
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
│  │ estimator registry (13 builtins)            │ │
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
```

## 4. 数据模型 (schema.py)

```python
class Master(BaseModel):
    name: str
    type: str                    # "can","spi","mipi_csi"...
    enabled: bool = True         # CAN 默认 false（6 款预设），用 --can-dbc 启用
    params: dict                 # 透传给对应 estimator
    verify: bool = False

class PipelineStage(BaseModel):
    name: str
    read_factor: float
    write_factor: float

class Pipeline(BaseModel):
    name: str
    type: str                    # "isp","npu","gpu","venc","vdec","display"
    mode: Literal["serial","parallel"]
    enabled: bool = True
    source: Optional[Union[str, list[str]]]  # master 名 / pipeline 名 / 列表 / null
    params: dict
    stages: list[PipelineStage]
    verify: bool = False

class DDRChannel(BaseModel):
    name: str
    theoretical_peak_mbps: float
    efficiency: float = 0.7
    read_write_ratio: float | None = None

class Topology(BaseModel):
    masters: list[Master]
    pipelines: list[Pipeline]
    ddr_channels: list[DDRChannel]
    alert_thresholds: dict = {"yellow": 0.6, "red": 0.8}

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

- 内置 13 个估算器，import 时自动注册
- 用户自定义：`@register("my_ip")`
- 系数集中在 `estimators/_coefficients.yaml`

## 6. 估算公式表

| type | 输入 | 公式 |
|---|---|---|
| can (DBC) | dbc_path, bus_id | Σ(DLC×8 / cycle_time) bit/s ×1.3(帧开销) → MB/s |
| can (load) | bitrate_mbps, load_pct | bitrate × load × 0.7(有效载荷) → MB/s |
| spi | clock_mhz, xfer_bytes, xfer_hz | min(clock×1e6/8, xfer_bytes×xfer_hz) |
| mipi_csi | w,h,fps,bpp,lanes,count | per_stream=w×h×fps×bpp/8；aggregate=per×count；校验 aggregate vs lane 上限 |
| mipi_dsi | w,h,fps,bpp,lanes,count | 同上（DSI 读 DDR，CSI 写 DDR） |
| usb | version, util_pct | nominal(480/5000/10000 Mbps) × util × 0.9 |
| eth | link_gbps, util_pct, mtu | link×util×mtu/(mtu+38) |
| flash | type, seq_r, seq_w, util, random_ratio | seq×util×[(1-r)+0.3r] |
| isp | w,h,fps,bpp,count,stages[] | frame=w×h×fps×bpp×count/8；各级 R=frame×read_factor, W=frame×write_factor；serial 取 max，parallel 取 sum |
| npu | params_mb, act_mb, inference_fps, tops_peak, sources[] | weight=params×fps；act=act_mb×2×fps；input=Σ各源(w×h×src_fps×bpp×count/8 或上游 write_bw)；read=weight+act/2+input |
| gpu | w,h,fps,bpp,overdraw | w×h×fps×bpp×overdraw/8 |
| display | w,h,fps,bpp 或 source_input_mbps | w×h×fps×bpp/8（或上游 write_bw） |
| venc | w,h,fps,bpp,codec 或 source_input_mbps | read=YUV=w×h×fps×bpp/8；write=bitstream=read/compression_ratio |
| vdec | w,h,fps,bpp,codec | read=bitstream=YUV/ratio；write=YUV |

### codec 压缩比（VENC/VDEC）
`codec` 参数选默认压缩比（_coefficients.yaml 可配）：
- h264: 30
- h265: 50
- av1: 70
- 单条覆盖：`params.compression_ratio: 40`

## 7. Predictor 算法（含 p2p 拓扑排序）

1. 先算所有 enabled master 的 estimate
2. 对 pipeline 做拓扑排序（DFS）：被 source 的 pipeline 先算
3. 环检测：A→B→A 报错 `cyclic pipeline dependency`
4. 每个 pipeline：
   - master source → 继承图像尺寸（w/h/fps/bpp/count），下游自算帧流
   - pipeline source → 拿上游 write_bw 作 source_input_mbps（pipeline 输出已转格式/缩放，无尺寸可继承）
   - 多源 [CSI0, ISP0] → master 贡献尺寸 + pipeline 贡献 write_bw，分别处理后累加
5. 全局汇总：R_demand = Σ read，W_demand = Σ write
6. 每项 assumptions 合并进全局段（带 level 分级）

## 8. Margin 评估

- `available_R = peak × efficiency × read_ratio`
- `available_W = peak × efficiency × write_ratio`
- `util_R = R_demand / available_R`，`util_W` 同理
- 告警：`util ≥ red(0.8)` → CRITICAL；`≥ yellow(0.6)` → WARN；否则 OK
- R/W 失衡：`|util_R - util_W| / max(...)` > 0.3 标记 IMBALANCE
- DDR 打满告警：`util ≥ 0.8` 进 assumptions 段（RED 级）

## 9. Assumptions 审计（分级）

每条 assumption 带 level：
- **RED**：激进 util>0.9 / lane 超 lane 上限 / NPU tops 超限 / DDR util>0.8
- **YEL**：非典型 stage 系数 / NPU fps < source fps / CAN load>0.7
- **INFO**：source 连线（声明事实）+ 未验证默认值（verify=true，来源说明非风险）

每项合并成一行（避免重复），取最严重 level 作行级 level。终端 Lv 列染色（RED=红 / YEL=黄 / i=暗青）。

> Phase 3 计划：verify 升级为动态验证状态——实测匹配的 item 自动从 INFO 变 OK（绿），不匹配变 RED。

## 10. CAN 健康报告（DBC 直读单独模式）

输入：单个 DBC 文件，不带 SoC。
输出（不含 DDR 评估）：
- 每总线：bitrate、总负载 kbps、负载率%
- Top-N 报文贡献（按 DLC×频率排序）
- 最坏帧延迟估算：`延迟 ≈ (最坏仲裁 + 最长报文传输) / (1 - 负载率)`
- 过载建议：>0.7 → 升级 bitrate / 拆分总线；>0.9 → 必须重构
- 支持 CAN-FD：`--can-bitrate 2000`（2Mbps），DBC 内 64 字节大帧自动处理

## 11. SoC 预设

`presets/<chip>.yaml` 含该芯片的：
- 典型外设清单（CAN 路数、MIPI 通道、USB 版本、ETH 速率、FLASH 类型）
- ISP 级数及默认系数 + source 连线（CSI1→ISP0）
- NPU TOPS / 典型模型参数 + source 连线（[CSI0, ISP0]）
- VENC/VDEC + source 连线（ISP0→VENC0）
- GPU/Display + source 连线（ISP0→DISP0）
- DDR 类型/速率/通道数 → 理论峰值
- **CAN 默认 enabled: false**（6 款非网关预设）；s32g（网关）保持启用
- 所有参数标 `verify: true`（进 assumptions 审计）

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
DDR0  peak 25600 MB/s  efficiency 0.7  available 17920
  Read demand 12450 MB/s  util 69.5%  [WARN]
  Write demand 8230 MB/s  util 45.9%  [OK]
  R/W balance: 1.52  [OK]

Top contributors (read+write):
  1. NPU0        4300 MB/s  20.5%  [from CSI0+ISP0]   ← source 名染色
  2. ISP0        3740 MB/s  17.8%  [from CSI1]
  ...

Assumptions (verify before trusting):
  Lv   Item    Message
  RED  NPU0    aggressive util_pct=0.95
  RED  DDR0    read util 96.4% >= 80% (DDR near full)
  YEL  CSI0    uses unverified default value
  i    ISP0    input from CSI1
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
└── full_menu.yaml          # 完整菜单模板
tests/
└── ...
```

## 16. 依赖

`pydantic>=2` `pyyaml` `rich` `cantools` `pytest`

## 17. 路线图（Phase 2-4）

- **Phase 2**：collect 层（perf/ddr-perf 采集 + parser + CLI collect）
- **Phase 3**：compare 引擎（偏差归因链）+ `buseval diff A B` scenario 对比 + verify 动态化（实测匹配的 item 自动从 INFO 变 OK，不匹配变 RED）
- **Phase 4**：系数自校准（实测反推 read_factor）+ Web UI（FastAPI 暴露 engine）

## 18. 开放问题（待 Phase 1 后复盘）

- ISP 各 stage 系数是否需要按厂商校准？
- NPU 估算用 TOPS 还是参数量为主？（当前两者都支持，tops 仅作 sanity check）
- 7 款 SoC 预设的真实参数需团队成员校准（当前用公开规格推算）
- NPU 逐层 `layers[]` 精确建模（当前聚合 params+act，后续可扩展）
