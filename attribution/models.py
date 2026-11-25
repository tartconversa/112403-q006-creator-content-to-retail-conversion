"""输入数据模型与通用小工具。

金额一律使用 :class:`decimal.Decimal`，避免浮点误差；日期为 ISO 字符串。
空单元格在 CSV 中表示来源未提供，统一解析为 ``None``。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")


def money(value: Any) -> Decimal:
    """解析金额并量化到分；空值/非法值由调用方先拦截。"""
    return Decimal(str(value)).quantize(TWO_PLACES)


def parse_iso_date(value: Optional[str]) -> Optional[date]:
    """解析 ISO ``YYYY-MM-DD``；空值返回 None，非法格式抛 ValueError。"""
    if value is None or str(value).strip() == "":
        return None
    return date.fromisoformat(str(value).strip())


def display_width(text: str) -> int:
    """东亚宽字符按两格计算，用于中文文本表格对齐。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    """按显示宽度右侧补空格。"""
    gap = width - display_width(text)
    return text + " " * max(gap, 0)


class JsonEncoder(json.JSONEncoder):
    """Decimal 量化后输出为数字，date 输出 ISO 字符串。"""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            if not o.is_finite():
                return None  # 严格 JSON 无 Infinity；零成本下 ROI 不可计算
            return float(o.quantize(TWO_PLACES))
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


@dataclass(frozen=True)
class Content:
    post: str
    creator: str
    channel: str
    campaign: str
    published: Optional[date]


@dataclass(frozen=True)
class Signal:
    record: str
    post: str
    metric: str
    value: Decimal
    window: Optional[str]
    raw_value: str


@dataclass(frozen=True)
class OrderRow:
    order_id: str
    person: str
    post: Optional[str]
    paid: Decimal
    refund: Decimal
    state: str
    paid_date: Optional[date] = None

    @property
    def net(self) -> Decimal:
        """退款后净销售额（不低于 0；若退款超出付款需单独告警）。"""
        return max(self.paid - self.refund, ZERO)


@dataclass(frozen=True)
class Contract:
    creator: str
    fixed: Decimal
    commission_rate: Decimal
    period: str  # 合同期，约定格式 YYYY-MM


@dataclass
class Dataset:
    """一次分析运行的全部输入与质量告警。"""

    contents: list[Content]
    signals: list[Signal]
    orders: list[OrderRow]
    contracts: list[Contract]
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def content_by_post(self) -> dict[str, Content]:
        return {c.post: c for c in self.contents}

    def contract_by_creator(self) -> dict[str, Contract]:
        return {c.creator: c for c in self.contracts}
