[English](README.md) | [中文](README_zh.md)

# buseval — SoC 带宽评估工具

预测和测量多核异构 SoC 的 DDR 带宽是否足够，覆盖外设（CAN/SPI/MIPI/USB/ETH/FLASH）与内部 pipeline（ISP/NPU/GPU/Display），并比对预测值与实测值。

## 为什么需要

现代 SoC 是多核异构系统，外设和加速器众多。项目前期往往靠经验估带宽，实测阶段才发现 DDR 不够，返工代价大。buseval 把"前期预测"和"实测对比"做成可重复、可审计的工程化流程。

## 总目标

1. **前期预测**：输入使用场景参数（DBC、分辨率/fps、bitrate/load、TOPS…），引擎估算各 master/pipeline 的读写带宽，汇总对比 DDR 可用带宽，给出余量和告警。
2. **实测采集**：通过 `perf` / `ddr-perf` 读取芯片实测带宽。
3. **预测 vs 实测对比**：量化偏差，归因到参数或公式，迭代校准。

## 现有目标（Phase 1）

仅做**前期预测**闭环，不做采集与对比。具体：

- 可插拔 estimator 引擎，内置 11 类外设/pipeline 估算器
- 三种入口：DBC 直读（CAN 健康报告）、SoC 预设、YAML 菜单
- 7 款主流芯片预设
- 假样例 DBC + 完整菜单模板
- 报告：Top-N 贡献、读写分离、assumptions 审计、breakdown 一句话注解
- CAN 健康报告（负载率/Top 报文/最坏帧延迟/过载建议）
- `lint` 漏项检查

## 快速开始

```bash
pip install buseval

# 1. 看样例（零配置）
buseval predict --soc rk3588
buseval predict --dbc examples/sample.dbc

# 2. DBC + 预设联动（完整评估）
buseval predict --soc tda4vh --dbc examples/sample.dbc

# 3. 自配 YAML
cp examples/full_menu.yaml my.yaml
buseval lint my.yaml
buseval predict -t my.yaml
```

## 路线图

- **Phase 1**：前期预测闭环（当前）
- **Phase 2**：实测采集（`perf` / `ddr-perf`）
- **Phase 3**：预测 vs 实测对比 + 归因链 + `scenario diff`
- **Phase 4**：系数自校准 + Web UI

## 支持的估算器

CAN(DBC) / CAN(load) / SPI / MIPI CSI / MIPI DSI / USB / ETH / FLASH(NAND/eMMC/UFS) / ISP / NPU / GPU / Display

## 支持的 SoC 预设

TI TDA4VH / NVIDIA Orin NX / 地平线 J5 / 高通 SA8155 / 瑞芯微 RK3588 / 全志 T527 / NXP S32G

## 文档

- 设计文档：[design.md](design.md)
- 估算系数：`src/buseval/estimators/_coefficients.yaml`
- 变更日志：`CHANGELOG.md`

## License

见 [LICENSE](LICENSE)。
