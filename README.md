# 达人内容长短链路评估与合作续约依据

合成达人内容、平台互动、匿名交易及合同结算样例，及一套仅依赖 Python 3
标准库的分析应用。金额为人民币元，全程使用 `Decimal` 计算，杜绝浮点误差。

All example identifiers and values are synthetic. CSV files use UTF-8. The
first row contains column names, and an empty cell means the source supplied
no value.

## 业务问题与应用立场

同一用户购买前可能看过多位创作者、隔数周才到店；平台只给汇总曝光，归因
窗口各异，合同又含固定费用与成交佣金。应用坚持以下不可妥协的规则：

1. **不重复计算**：只提供末触、首触、路径均分三种可解释模型；任一模型内
   每笔订单恰好分配一次，平台汇总声称的成交绝不与订单表相加。
2. **不把曝光当顾客**：`views`/`visits` 等次数口径只做对账背景，禁止换算
   成独立顾客数，未登记含义的指标只原样展示并产生告警。
3. **展示可确认与无法归属的范围**：没有个人级触点的订单进入"无法归属池"
   单列；情景上限对该池的分摊必须显式标注为假设，不与确认值并列陈述。
4. **已发生成本不消失**：合同固定费用即使在归因争议下也保留在成本中；
   佣金按订单净销售（退款后）逐单结算，并区分已结/未结。
5. **建议可更新、决策可追溯**：待签约建议随平台补报或退款重算；已签合作
   写入只读证据快照，输入变化只提示漂移，绝不回写历史决策。

## 输入文件

- `content.csv`：`post,creator,channel,campaign,published`
- `signals.csv`：`record,post,metric,value,window`
- `contracts.csv`：`creator,fixed,commission,period`（同一达人可按不同
  `period` 有多份合同，主键为达人+合同期）
- `orders.csv`：`order_id,person,post,paid,refund,state`，可选列
  `paid_date`（提供后才能做发布→购买滞后窗分析）；`post` 留空表示来源
  未提供可确认触点，订单进入无法归属池。

## 使用方法

```bash
# 校验结构与引用，软告警输出到 stderr
python3 -m attribution validate samples

# 文本复盘报告（八个章节，见下）
python3 -m attribution report samples

# 机器可读 JSON（严格 JSON，零成本下不可计算的 ROI 输出 null）
python3 -m attribution report samples --json -o report.json

# 签署一份合作决策快照（写入后只读，同 ID 重签被拒绝）
python3 -m attribution sign samples D-2026-001 K1 linear \
    --store decisions --signed-on 2026-11-20 --note "首次合作复盘"

# 平台补报/退款后检查已签决策与当前输入的漂移
python3 -m attribution decisions samples --store decisions
```

在隔离环境处理不可信输入目录时，可用 `python3 -I` 配合绝对路径入口脚本，
脚本与数据目录分离，避免数据目录中的同名模块被导入。

## 报告章节

1. 数据质量与口径告警（未知指标/渠道、悬空引用、退款异常、缺合同等）
2. 可确认触点与无法归属范围、跨创作者触点重叠
3. 三种归因解释下的达人净销售、确认区间与情景上限（含假设说明）
4. 平台汇总指标对账与渠道可比性（次数口径 vs 订单口径，差额只展示）
5. 内容发布到购买的滞后窗（7/14/30 天，缺日期或负滞后显式标注）
6. 合同固定费用、已结/未结佣金与已发生成本
7. 续约/待签约建议（按最不利归因解释的净 ROI 分级，附数据保留意见）
8. 已签合作决策快照与输入漂移状态

## 代码结构

| 模块 | 职责 |
| --- | --- |
| `attribution/loading.py` | CSV 读取、硬错误/软告警、输入指纹 |
| `attribution/journeys.py` | 按匿名用户重建可确认触点旅程 |
| `attribution/attribution.py` | 三种归因模型、确认区间、平台对账 |
| `attribution/timing.py` | 发布→购买滞后窗 |
| `attribution/finance.py` | 固定费用、退款后佣金、结算状态 |
| `attribution/recommend.py` | 多解释下的续约建议与保留意见 |
| `attribution/decisions.py` | 只读决策快照与漂移检测 |
| `attribution/report.py` | 文本/JSON 报告组装 |
| `attribution/cli.py` | 命令行入口 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖输入校验与告警、三种模型的逐单守恒（每订单分数合计为 1、各模型
达人净销售+无法归属=总净销售）、滞后窗边界、退款与未结佣金、多合同期费用
累加、决策快照一次性写入与漂移、CLI 退出码与严格 JSON。
