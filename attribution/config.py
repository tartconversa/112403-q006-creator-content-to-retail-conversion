"""分析师确认的字段含义、渠道关联强度与归因窗口配置。

所有默认含义均可在调用方覆盖；输入中出现未登记的指标或渠道时，
应用不会猜测，而是生成质量告警要求市场分析师确认。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    """平台指标的确认含义。

    kind:
      - ``audience``  人群口径（可近似独立顾客，但仍可能去重不彻底）
      - ``impression`` 曝光/访问口径（次数，绝不能当作独立顾客数）
      - ``order_count`` 平台声称的成交订单数
      - ``sales``     平台声称的成交金额
    comparable_to_orders: 该指标能否与订单级数据直接相加比较。
    """

    kind: str
    comparable_to_orders: bool
    description: str


# 市场分析师确认的平台事件含义（样例数据所覆盖的指标）。
DEFAULT_METRICS: dict[str, MetricSpec] = {
    "views": MetricSpec(
        kind="impression",
        comparable_to_orders=False,
        description="短视频播放次数，曝光口径，非独立顾客数",
    ),
    "visits": MetricSpec(
        kind="impression",
        comparable_to_orders=False,
        description="门店体验到访次数，渠道汇总口径，无个人级身份关联",
    ),
    "platform-orders": MetricSpec(
        kind="order_count",
        comparable_to_orders=False,
        description="平台按其自有归因窗口声称的成交订单数，可能与订单表重复",
    ),
    "platform-sales": MetricSpec(
        kind="sales",
        comparable_to_orders=False,
        description="平台按其自有归因窗口声称的成交金额，可能与订单表重复",
    ),
}

# 电商与门店运营确认的订单/到店关联强度。
# strong   : 订单行带 post，可确认到具体内容与达人
# weak     : 仅渠道汇总（如门店到访），只能用于背景，不能逐单归因
# none     : 无任何关联
DEFAULT_LINK_STRENGTH: dict[str, str] = {
    "short-video": "strong",
    "live": "strong",
    "store-event": "weak",
}

# 合作复盘默认展示的发布→购买滞后窗（天）。
DEFAULT_LAG_WINDOWS: tuple[int, ...] = (7, 14, 30)

# 续约分级的净 ROI 阈值（净销售/已发生成本）。
ROI_RENEW = 1.0
ROI_REVIEW = 0.3
