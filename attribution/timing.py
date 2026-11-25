"""内容发布到购买的滞后窗分析。

仅当订单表提供 ``paid_date`` 且内容有发布日期时才可计算。
日期缺失时返回 ``computable=False`` 并说明原因，绝不用顺序或猜测填充。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .config import DEFAULT_LAG_WINDOWS
from .models import Dataset, ZERO


@dataclass
class LagBucket:
    window_days: int
    order_ids: list[str] = field(default_factory=list)
    gross: Decimal = ZERO
    refund: Decimal = ZERO
    net: Decimal = ZERO


@dataclass
class LagReport:
    computable: bool
    reason: str
    windows: list[int]
    buckets: list[LagBucket]
    missing_date_orders: list[str]
    negative_lag_orders: list[str]  # 付款早于发布，数据质量异常
    lags: dict[str, int]  # order_id -> 滞后天数


def lag_report(dataset: Dataset, windows: tuple[int, ...] = DEFAULT_LAG_WINDOWS) -> LagReport:
    content_index = dataset.content_by_post()
    if not any(order.paid_date is not None for order in dataset.orders):
        return LagReport(
            computable=False,
            reason="订单表未提供 paid_date 列或全部为空，无法计算发布→购买滞后",
            windows=list(windows),
            buckets=[],
            missing_date_orders=[o.order_id for o in dataset.orders],
            negative_lag_orders=[],
            lags={},
        )

    ordered_windows = sorted(windows)
    buckets = [LagBucket(window_days=w) for w in ordered_windows]
    missing: list[str] = []
    negative: list[str] = []
    lags: dict[str, int] = {}

    for order in dataset.orders:
        if order.post is None or order.paid_date is None:
            missing.append(order.order_id)
            continue
        published = content_index[order.post].published
        if published is None:
            missing.append(order.order_id)
            continue
        lag = (order.paid_date - published).days
        lags[order.order_id] = lag
        if lag < 0:
            negative.append(order.order_id)
        for bucket in buckets:
            if lag <= bucket.window_days:
                bucket.order_ids.append(order.order_id)
                bucket.gross += order.paid
                bucket.refund += order.refund
                bucket.net += order.net

    return LagReport(
        computable=True,
        reason="",
        windows=ordered_windows,
        buckets=buckets,
        missing_date_orders=sorted(set(missing)),
        negative_lag_orders=sorted(negative),
        lags=lags,
    )
