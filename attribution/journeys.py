"""按匿名用户重建可确认的触点旅程。

订单行上的 ``post`` 是唯一经过电商/门店运营确认、可到个人的强关联。
平台汇总指标（播放、到访、平台归因订单）没有个人身份，不进入旅程；
没有 post 的订单进入"无法归属池"，绝不硬塞给任何达人。
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_LINK_STRENGTH
from .models import Content, Dataset, OrderRow


@dataclass(frozen=True)
class Touchpoint:
    person: str
    order_id: str
    post: str
    creator: str
    channel: str
    link_strength: str
    paid_date_order_index: int  # 同一用户订单中的顺序（无日期时的稳定排序）


@dataclass
class Journey:
    person: str
    touchpoints: list[Touchpoint]
    order_ids: list[str]

    @property
    def creators(self) -> list[str]:
        seen: list[str] = []
        for tp in self.touchpoints:
            if tp.creator not in seen:
                seen.append(tp.creator)
        return seen

    @property
    def is_overlap(self) -> bool:
        """同一用户在购买前接触过多位创作者。"""
        return len(self.creators) > 1


def build_journeys(dataset: Dataset) -> dict[str, Journey]:
    """以 person 为键汇总可确认触点；按付款日期排序，缺日期时按文件顺序。"""
    content_index = dataset.content_by_post()
    per_person: dict[str, list[tuple[OrderRow, int]]] = {}
    for index, order in enumerate(dataset.orders):
        if order.post is None:
            continue  # 无法归属订单不进入任何旅程
        per_person.setdefault(order.person, []).append((order, index))

    journeys: dict[str, Journey] = {}
    for person, entries in per_person.items():
        entries.sort(key=lambda pair: (pair[0].paid_date or pair[1], pair[1]))
        touchpoints: list[Touchpoint] = []
        order_ids: list[str] = []
        for seq, (order, original_index) in enumerate(entries):
            content: Content = content_index[order.post]
            strength = DEFAULT_LINK_STRENGTH.get(content.channel, "none")
            touchpoints.append(
                Touchpoint(
                    person=person,
                    order_id=order.order_id,
                    post=order.post,
                    creator=content.creator,
                    channel=content.channel,
                    link_strength=strength,
                    paid_date_order_index=seq,
                )
            )
            order_ids.append(order.order_id)
        journeys[person] = Journey(person=person, touchpoints=touchpoints, order_ids=order_ids)
    return journeys


def unattributed_orders(dataset: Dataset) -> list[OrderRow]:
    """来源未提供可确认触点的订单（展示为无法归属范围，不参与达人归因）。"""
    return [order for order in dataset.orders if order.post is None]


def overlap_persons(journeys: dict[str, Journey]) -> list[str]:
    """触点跨多位创作者的用户，用于说明末触/首触的争议来源。"""
    return sorted(person for person, journey in journeys.items() if journey.is_overlap)
