# busval 设计文档

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
4. **可审计**：所有默认值/激进假设进 assumptions 段标红，预测结果带 breakdown + dominant_factor。
5. **峰值汇总 + 读写分离**：master 峰值带宽加总对比 DDR；读写分别评估。
6. **参数与代码分离**：估算系数集中在 `_coefficients.yaml`，可校准不动代码。

## 3. 系统架构

```
┌──────────────────────────────────────────────────┐
│  CLI (cli.py)                                     │
│  predict / lint                                   │
├──────────────────────────────────────────────────┤
│  入口层                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ DBC 直读 │ │ SoC 预设 │ │ YAML 菜单(loader)│ │
│  └────┬─────┘ └────┬─────┘ └────────┬─────────┘ │
│       └─────────────┴────────────────┘           │
│                    ▼                              │
├──────────────────────────────────────────────────┤
│  核心引擎                                         │
│  ┌─────────────────────────────────────────────┐ │
│  │ estimator registry                          │ │
│  │  ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐  │ │
│  │  │ CAN  ││ SPI  ││ MIPI ││ ISP  ││ NPU  │  │ │
│  │  └──────┘└──────┘└──────┘└──────┘└──────┘  │ │
│  └─────────────────────────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐               │
│  │ predictor    │ │ margin       │               │
│  │ (峰值汇总)   │ │ (效率+告警)  │               │
│  └──────────────┘ └──────────────┘               │
├──────────────────────────────────────────────────┤
│  报告层                                           │
│  ┌──────────────┐ ┌──────────────┐               │
│  │ terminal     │ │ structured   │               │
│  │ (rich 表格)  │ │ (JSON/YAML)  │               │
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
    enabled: bool = True
    params: dict                 # 透传给对应 estimator

class PipelineStage(BaseModel):
    name: str
    read_factor: float           # 相对帧流倍数
    write_factor: float

class Pipeline(BaseModel):
    name: str
    type: str                    # "isp","npu","gpu"
    mode: Literal["serial","parallel"]
    params: dict
    stages: list[PipelineStage] = []   # ISP 用；NPU/GPU 可不用

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
    breakdown: dict              # 推导明细
    dominant_factor: str         # 一句话注解
    assumptions: list[str] = []  # 该项依赖的激进假设
```

## 5. Estimator Registry

```python
class Estimator(Protocol):
    type: str
    params_schema: type[BaseModel]
    def estimate(self, params: dict) -> BandwidthEstimate: ...

def register(type_name: str): ...
def get_estimator(type_name: str) -> Estimator: ...
```

- 内置估算器在 `estimators/builtins/`，import 时自动注册
- 用户自定义：实现 `Estimator` 并 `@register("my_ip")`
- 系数集中在 `estimators/_coefficients.yaml`

## 6. 估算公式表

| type | 输入 | 公式 |
|---|---|---|
| can (DBC) | dbc_path, bus_id | Σ(DLC×8 / cycle_time) bit/s ×1.3(帧开销) → MB/s |
| can (load) | bitrate_mbps, load_pct | bitrate × load × 0.7(有效载荷) → MB/s |
| spi | clock_mhz, xfer_bytes, xfer_hz | min(clock×1e6/8, xfer_bytes×xfer_hz) |
| mipi_csi | w,h,fps,bpp,lanes | w×h×fps×bpp/8 → MB/s；校验 lanes 上限 |
| mipi_dsi | w,h,fps,bpp,lanes | 同上 |
| usb | version, util_pct | nominal(480/5000/10000 Mbps) × util × 0.9 |
| eth | link_gbps, util_pct, mtu | link×util×mtu/(mtu+38) |
| flash | type, seq_r, seq_w, util, random_ratio | seq×util×[(1-r)+0.3r] |
| isp | w,h,fps,in_format,stages[] | frame=w×h×fps×bpp/8；各级 R=frame×read_factor, W=frame×write_factor；serial 取 max，parallel 取 sum |
| npu | params_mb, act_mb, fps, tops_peak | weight=params×fps/latency；act=act_mb×fps×2；校验 tops |
| gpu | w,h,fps,bpp,overdraw | w×h×fps×bpp×overdraw/8 |
| display | w,h,fps,bpp | w×h×fps×bpp/8 |

## 7. Predictor 算法

1. 对每个 enabled master：调用 `estimator.estimate(params)` 得 `BandwidthEstimate`
2. 对每个 pipeline：同上，内部按 `mode` 汇总各级
3. 全局汇总：`R_demand = Σ read_bw`，`W_demand = Σ write_bw`
4. 每项的 `assumptions` 合并进全局 assumptions 段
5. Top-N 排序：按 `read+write` 降序，标贡献%

## 8. Margin 评估

- `available_R = peak × efficiency × read_ratio`
- `available_W = peak × efficiency × write_ratio`
- `util_R = R_demand / available_R`，`util_W` 同理
- 告警：`util ≥ red(0.8)` → CRITICAL；`≥ yellow(0.6)` → WARN；否则 OK
- R/W 失衡指标：`|util_R - util_W| / max(...)` > 0.3 标记 IMBALANCE

## 9. Assumptions 审计

每项 estimate 可声明 assumptions，规则：
- util_pct > 0.9 / load_pct > 0.7 → "激进利用率"
- 使用了默认典型值（`_verify: true` 未改）→ "未验证默认值"
- MIPI lanes 不足 → "带宽超 lane 上限"
- DDR util > 0.8 → "DDR 接近打满"
- stage 系数超典型范围 2 倍 → "非典型系数"

全局汇总到报告 `assumptions:` 段，终端红色标注。

## 10. CAN 健康报告（DBC 直读单独模式）

输入：单个 DBC 文件，不带 SoC。
输出（不含 DDR 评估）：
- 每总线：bitrate、总负载 kbps、负载率%
- Top-N 报文贡献（按 DLC×频率排序）
- 最坏帧延迟估算：`延迟 ≈ (最坏仲裁 + 最长报文传输) / (1 - 负载率)`，负载率>0.7 用扩展上限
- 过载建议：>0.7 → 升级 bitrate / 拆分总线；>0.9 → 必须重构

## 11. SoC 预设

`presets/<chip>.yaml` 含该芯片的：
- 典型外设清单（CAN 路数、MIPI 通道、USB 版本、ETH 速率、FLASH 类型）
- ISP 级数及默认系数
- NPU TOPS / 典型模型参数
- GPU/Display 默认配置
- DDR 类型/速率/通道数 → 理论峰值
- 所有参数标 `_verify: true`（进 assumptions 审计）

7 款：tda4vh / orin_nx / j5 / sa8155 / rk3588 / t527 / s32g

## 12. Lint 规则

`lint_rules.yaml`，可扩展：
- 类别全缺：无 Display 且无 NPU 且无 GPU → 警告
- 拓扑矛盾：有 CSI 无 ISP / 有 NPU 无 weight 来源（FLASH/DDR 预载） → 警告
- 必填缺失：无 DDR 通道 → 报错
- 参数越界：load_pct∈[0,1]、lanes∈{1,2,4} → 报错

## 13. 报告格式

### 13.1 终端（rich 表格）
```
DDR Bandwidth Report
═══════════════════════════════════════════════════
DDR0  peak 25600 MB/s  efficiency 0.7  available 17920
  Read demand 12450 MB/s  util 69.5%  [WARN]
  Write demand 8230 MB/s  util 45.9%  [OK]
  R/W balance: 1.52  [OK]

Top contributors (read+write):
  1. NPU0        4300 MB/s  20.5%  [主因: act 30MB×100fps]
  2. ISP0        3740 MB/s  17.8%  [主因: demosaic stage read×1.5]
  3. CSI0        0960 MB/s   4.6%  [主因: 1920×1080×30×12bpp]
  ...

Assumptions (需确认):
  [RED]  NPU0    util_pct=0.95 激进利用率
  [RED]  CSI0    lanes=1 带宽超 lane 上限
  [YEL]  ETH0    使用默认 util_pct=0.4 未验证
```

### 13.2 结构化 (report.yaml / report.json)
含时间戳、拓扑哈希、逐项 estimate（含 breakdown）、汇总、assumptions、verdict。

## 14. CLI

```
busval predict --dbc f.dbc                    # CAN 健康报告
busval predict --soc <chip>                   # 预设评估
busval predict --soc <chip> --dbc f.dbc       # DBC+预设
busval predict -t my.yaml                     # 自配
busval lint -t my.yaml
```

公共参数：`-o/--output report.yaml` `--format {table,json,yaml}` `--no-color`

## 15. 目录结构

```
src/busval/
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
│       └── gpu_display.py
├── engine/
│   ├── predictor.py
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
    ├── terminal.py
    └── structured.py
examples/
├── sample.dbc
└── full_menu.yaml
tests/
└── ...
```

## 16. 依赖

`pydantic>=2` `pyyaml` `rich` `cantools` `pytest`

## 17. 路线图（Phase 2-4）

- **Phase 2**：collect 层（perf/ddr-perf 采集 + parser + CLI collect）
- **Phase 3**：compare 引擎（偏差归因链）+ `busval diff A B` scenario 对比
- **Phase 4**：系数自校准（实测反推 read_factor）+ Web UI（FastAPI 暴露 engine）

## 18. 开放问题（待 Phase 1 后复盘）

- ISP 各 stage 系数是否需要按厂商校准？
- NPU 估算用 TOPS 还是参数量为主？
- 7 款 SoC 预设的真实参数需团队成员校准（当前用公开规格推算）
