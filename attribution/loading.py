"""读取四类 CSV 输入并做结构与引用校验。

校验分两级：

- 硬错误（缺文件、表头不符、数字/日期无法解析、主键重复）抛
  :class:`InputError`，本次分析不产出结论；
- 软告警（未知指标/渠道、悬空引用、缺合同、退款大于付款等）收集到
  ``Dataset.warnings``，报告必须原样展示，不能静默丢弃。
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Optional

from .config import DEFAULT_LINK_STRENGTH, DEFAULT_METRICS
from .models import Contract, Content, Dataset, OrderRow, Signal, money, parse_iso_date

EXPECTED_HEADERS = {
    "content": ["post", "creator", "channel", "campaign", "published"],
    "signals": ["record", "post", "metric", "value", "window"],
    "contracts": ["creator", "fixed", "commission", "period"],
    "orders": ["order_id", "person", "post", "paid", "refund", "state"],
}
# 订单表允许的扩展列：订单付款日期，使发布→购买滞后分析成为可能。
OPTIONAL_ORDER_COLUMNS = {"paid_date"}


class InputError(ValueError):
    """输入文件无法支撑分析。"""


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise InputError(f"输入文件不存在：{path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames is None:
            raise InputError(f"文件缺少表头：{path}")
        rows = list(reader)
    return list(reader.fieldnames), rows


def _require(row: dict[str, str], key: str, where: str) -> str:
    value = (row.get(key) or "").strip()
    if value == "":
        raise InputError(f"{where} 存在必填字段 {key} 为空的记录：{row}")
    return value


def _optional(row: dict[str, str], key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _fingerprint(directory: Path, names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("utf-8"))
        digest.update((directory / f"{name}.csv").read_bytes())
    return digest.hexdigest()


def load_dataset(directory: str | Path) -> Dataset:
    """从目录读取 content/signals/contracts/orders 四份 CSV。"""
    directory = Path(directory)
    warnings: list[str] = []

    headers: dict[str, list[str]] = {}
    raw: dict[str, list[dict[str, str]]] = {}
    for name, expected in EXPECTED_HEADERS.items():
        cols, rows = _read_csv(directory / f"{name}.csv")
        extra = [c for c in cols if c not in expected and not (
            name == "orders" and c in OPTIONAL_ORDER_COLUMNS
        )]
        missing = [c for c in expected if c not in cols]
        if missing:
            raise InputError(f"{name}.csv 缺少列：{missing}；实际列：{cols}")
        if extra:
            warnings.append(f"{name}.csv 含未登记列 {extra}，应用不会使用这些字段")
        headers[name] = cols
        raw[name] = rows
        if not rows:
            raise InputError(f"{name}.csv 没有数据行")

    # ---- content -------------------------------------------------------
    contents: list[Content] = []
    seen_posts: set[str] = set()
    for row in raw["content"]:
        post = _require(row, "post", "content.csv")
        if post in seen_posts:
            raise InputError(f"content.csv 中 post 重复：{post}")
        seen_posts.add(post)
        channel = _require(row, "channel", "content.csv")
        if channel not in DEFAULT_LINK_STRENGTH:
            warnings.append(
                f"内容 {post} 的渠道 {channel} 未确认关联强度，按不可逐单关联处理"
            )
        published = parse_iso_date(_optional(row, "published"))
        if _optional(row, "published") and published is None:
            raise InputError(f"content.csv 中 {post} 的发布日期无法解析")
        contents.append(
            Content(
                post=post,
                creator=_require(row, "creator", "content.csv"),
                channel=channel,
                campaign=_require(row, "campaign", "content.csv"),
                published=published,
            )
        )

    # ---- signals -------------------------------------------------------
    signals: list[Signal] = []
    seen_records: set[str] = set()
    for row in raw["signals"]:
        record = _require(row, "record", "signals.csv")
        if record in seen_records:
            raise InputError(f"signals.csv 中 record 重复：{record}")
        seen_records.add(record)
        post = _require(row, "post", "signals.csv")
        if post not in seen_posts:
            warnings.append(f"信号 {record} 指向不存在的内容 {post}，该信号保留但无法关联")
        metric = _require(row, "metric", "signals.csv")
        if metric not in DEFAULT_METRICS:
            warnings.append(
                f"信号 {record} 的指标 {metric} 未经市场分析师确认含义，"
                "仅原样展示，不参与归因或口径比较"
            )
        raw_value = _require(row, "value", "signals.csv")
        try:
            value = money(raw_value)
        except Exception as exc:  # noqa: BLE001 - 任何解析失败都转为输入错误
            raise InputError(f"信号 {record} 的数值无法解析：{raw_value}") from exc
        signals.append(
            Signal(
                record=record,
                post=post,
                metric=metric,
                value=value,
                window=_optional(row, "window"),
                raw_value=raw_value,
            )
        )

    # ---- contracts -----------------------------------------------------
    # 同一达人可在不同合同期各有一份合同，主键为 (creator, period)。
    contracts: list[Contract] = []
    seen_contracts: set[tuple[str, str]] = set()
    for row in raw["contracts"]:
        creator = _require(row, "creator", "contracts.csv")
        period = _require(row, "period", "contracts.csv")
        key = (creator, period)
        if key in seen_contracts:
            raise InputError(f"contracts.csv 中达人 {creator} 在 {period} 的合同重复")
        seen_contracts.add(key)
        if len(period) != 7 or period[4] != "-":
            warnings.append(f"达人 {creator} 的合同期 {period} 不是 YYYY-MM 格式")
        try:
            fixed = money(_require(row, "fixed", "contracts.csv"))
            rate = money(_require(row, "commission", "contracts.csv"))
        except Exception as exc:  # noqa: BLE001
            raise InputError(f"达人 {creator} 的合同金额无法解析") from exc
        contracts.append(
            Contract(creator=creator, fixed=fixed, commission_rate=rate, period=period)
        )

    # ---- orders --------------------------------------------------------
    orders: list[OrderRow] = []
    seen_orders: set[str] = set()
    has_paid_date = "paid_date" in headers["orders"]
    for row in raw["orders"]:
        order_id = _require(row, "order_id", "orders.csv")
        if order_id in seen_orders:
            raise InputError(f"orders.csv 中 order_id 重复：{order_id}")
        seen_orders.add(order_id)
        post = _optional(row, "post")
        if post is not None and post not in seen_posts:
            warnings.append(f"订单 {order_id} 指向不存在的内容 {post}，降级为无法归属")
            post = None
        try:
            paid = money(_require(row, "paid", "orders.csv"))
            refund = money(_require(row, "refund", "orders.csv"))
        except Exception as exc:  # noqa: BLE001
            raise InputError(f"订单 {order_id} 的金额无法解析") from exc
        if refund > paid:
            warnings.append(
                f"订单 {order_id} 退款 {refund} 大于付款 {paid}，净销售按 0 计并需人工核对"
            )
        paid_date = parse_iso_date(_optional(row, "paid_date")) if has_paid_date else None
        if has_paid_date and _optional(row, "paid_date") and paid_date is None:
            raise InputError(f"订单 {order_id} 的 paid_date 无法解析")
        orders.append(
            OrderRow(
                order_id=order_id,
                person=_require(row, "person", "orders.csv"),
                post=post,
                paid=paid,
                refund=refund,
                state=_require(row, "state", "orders.csv"),
                paid_date=paid_date,
            )
        )

    known_creators = {c.creator for c in contents}
    contracted_creators = {c.creator for c in contracts}
    for creator in contracted_creators - known_creators:
        warnings.append(f"合同中的达人 {creator} 没有任何发布内容，固定费用仍计入已发生成本")
    for content in contents:
        if content.creator not in contracted_creators:
            warnings.append(f"达人 {content.creator} 缺少合同，佣金无法结算，需补签确认")

    return Dataset(
        contents=contents,
        signals=signals,
        orders=orders,
        contracts=contracts,
        warnings=warnings,
        fingerprint=_fingerprint(directory, tuple(EXPECTED_HEADERS)),
    )
