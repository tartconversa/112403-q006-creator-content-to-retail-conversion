"""多触点归因与平台汇总对账。

设计原则（对应业务约束）：

1. 只提供可解释的归因模型：末触（last_touch）、首触（first_touch）、
   均分（linear，按该订单之前路径上的不同创作者等分）。
2. 每个模型都只分配订单表中可确认的金额，三者之和互不混用；
   同一订单在任一模型内恰好被分配一次，杜绝重复计算。
3. 没有触点的订单只进入"无法归属池"，再以显式标注假设的情景上限
   分摊给达人，绝不与确认值并列陈述为事实。
4. 平台汇总指标按分析师确认的口径对账：次数口径禁止换算成顾客数，
   平台声称订单与订单表的差额只展示、不强行解释、不相加。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .config import DEFAULT_METRICS
from .journeys import Journey, overlap_persons
from .models import TWO_PLACES, ZERO, Dataset

MODELS = ("last_touch", "first_touch", "linear")
MODEL_LABELS = {
    "last_touch": "末触归因",
    "first_touch": "首触归因",
    "linear": "路径均分",
}
ONE = Decimal("1")


@dataclass
class Amounts:
    gross: Decimal = ZERO
    refund: Decimal = ZERO
    net: Decimal = ZERO
    order_count: int = 0
    credited_orders: Decimal = ZERO  # 按分数折算的订单当量，仅作辅助

    def add(self, gross: Decimal, refund: Decimal, net: Decimal, fraction: Decimal) -> None:
        self.gross += gross
        self.refund += refund
        self.net += net
        self.credited_orders += fraction


@dataclass
class PlatformClaim:
    record: str
    post: str
    metric: str
    window: Optional[str]
    platform_value: Decimal
    order_table_value: Decimal  # 同 post 在订单表可确认的订单数（次数口径时为 0）
    gap: Decimal
    note: str


@dataclass
class AttributionResult:
    # order_id -> model -> creator -> 分配分数（同模型内合计为 1）
    fractions: dict[str, dict[str, dict[str, Decimal]]]
    model_totals: dict[str, dict[str, Amounts]]
    post_totals: dict[str, dict[str, Amounts]]
    unattributed: Amounts
    unattributed_ids: list[str]
    confirmed_range: dict[str, tuple[Decimal, Decimal]]  # creator -> (三模型最低, 最高)
    upper_scenario: dict[str, Amounts]  # 含无法归属池情景分摊
    upper_assumption: str
    overlap: dict[str, object]
    platform_claims: list[PlatformClaim]
    totals: Amounts
    creators: list[str]


def _split(amount: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """按权重切分金额到分，尾差并入最大份额，保证合计等于原金额。"""
    if not weights:
        return {}
    total_weight = sum(weights.values(), ZERO)
    shares = {key: (amount * w / total_weight).quantize(TWO_PLACES) for key, w in weights.items()}
    residual = amount - sum(shares.values(), ZERO)
    if residual:
        biggest = max(shares, key=lambda key: (shares[key], key))
        shares[biggest] += residual
    return shares


def _path_creators(journey: Journey, order_index: int) -> list[str]:
    """该订单发生时（含当前触点）已经过的不同创作者，按接触顺序。"""
    seen: list[str] = []
    for tp in journey.touchpoints[: order_index + 1]:
        if tp.creator not in seen:
            seen.append(tp.creator)
    return seen


def attribute(dataset: Dataset, journeys: dict[str, Journey]) -> AttributionResult:
    creators = sorted({c.creator for c in dataset.contents})
    content_index = dataset.content_by_post()
    order_index = {o.order_id: o for o in dataset.orders}

    fractions: dict[str, dict[str, dict[str, Decimal]]] = {}
    model_totals = {m: {k: Amounts() for k in creators} for m in MODELS}
    post_totals = {m: {p.post: Amounts() for p in dataset.contents} for m in MODELS}

    for person, journey in journeys.items():
        for idx, tp in enumerate(journey.touchpoints):
            order = order_index[tp.order_id]
            path = _path_creators(journey, idx)
            weights_by_model = {
                "last_touch": {path[-1]: ONE},
                "first_touch": {path[0]: ONE},
                "linear": {creator: ONE for creator in path},
            }
            fractions[order.order_id] = {}
            for model, weights in weights_by_model.items():
                total_weight = sum(weights.values(), ZERO)
                score = {creator: w / total_weight for creator, w in weights.items()}
                fractions[order.order_id][model] = score
                net_shares = _split(order.net, weights)
                gross_shares = _split(order.paid, weights)
                refund_shares = _split(order.refund, weights)
                for creator, fraction in score.items():
                    target = model_totals[model][creator]
                    target.add(
                        gross_shares[creator],
                        refund_shares[creator],
                        net_shares[creator],
                        fraction,
                    )
                # 帖子口径记整笔订单（每个模型每订单恰好一次），不按创作者重复累加
                post_target = post_totals[model][order.post]
                post_target.add(order.paid, order.refund, order.net, ONE)
                post_target.order_count += 1
    # 订单数：按实际触达（不按分数）统计独立订单数
    touched_orders: dict[str, set[str]] = {creator: set() for creator in creators}
    for order_id, by_model in fractions.items():
        for creator in by_model["linear"]:
            touched_orders[creator].add(order_id)
    for model in MODELS:
        for creator in creators:
            model_totals[model][creator].order_count = len(touched_orders[creator])

    # ---- 无法归属池 ----------------------------------------------------
    unattributed = Amounts()
    unattributed_ids: list[str] = []
    for order in dataset.orders:
        unattributed.gross += order.paid
        unattributed.refund += order.refund
        unattributed.net += order.net
        unattributed.order_count += 1
        if order.post is None:
            unattributed_ids.append(order.order_id)
    # totals 先记全量，再减去无归属部分
    totals = Amounts(
        gross=unattributed.gross,
        refund=unattributed.refund,
        net=unattributed.net,
        order_count=unattributed.order_count,
    )
    attributed_pool = Amounts()
    for creator, amounts in model_totals["linear"].items():
        attributed_pool.gross += amounts.gross
        attributed_pool.refund += amounts.refund
        attributed_pool.net += amounts.net
    unattributed = Amounts(
        gross=totals.gross - attributed_pool.gross,
        refund=totals.refund - attributed_pool.refund,
        net=totals.net - attributed_pool.net,
        order_count=len(unattributed_ids),
    )

    # ---- 确认区间与情景上限 --------------------------------------------
    confirmed_range: dict[str, tuple[Decimal, Decimal]] = {}
    upper_scenario: dict[str, Amounts] = {}
    confirmed_net_total = sum(
        (model_totals["linear"][c].net for c in creators), ZERO
    )
    assumption = (
        "情景上限假设：无法归属池按各达人在均分模型下的确认净销售占比分摊；"
        "该假设无个人级证据，仅用于展示范围，不代表实际贡献"
    )
    # 先算各达人对无法归属池的分摊，再把舍入尾差并入最大份额，保证合计守恒
    pool_shares: dict[str, Decimal] = {}
    for creator in creators:
        if confirmed_net_total > 0:
            share = model_totals["linear"][creator].net / confirmed_net_total
        else:
            share = ONE / len(creators) if creators else ZERO
        pool_shares[creator] = (unattributed.net * share).quantize(TWO_PLACES)
    if creators:
        residual = unattributed.net - sum(pool_shares.values(), ZERO)
        if residual:
            biggest = max(pool_shares, key=lambda key: (pool_shares[key], key))
            pool_shares[biggest] += residual
    for creator in creators:
        nets = [model_totals[model][creator].net.quantize(TWO_PLACES) for model in MODELS]
        confirmed_range[creator] = (min(nets), max(nets))
        base = model_totals["linear"][creator]
        upper = Amounts(
            gross=base.gross,
            refund=base.refund,
            net=(base.net + pool_shares[creator]).quantize(TWO_PLACES),
            order_count=base.order_count,
        )
        upper_scenario[creator] = upper

    # ---- 触点重叠 ------------------------------------------------------
    overlap_users = overlap_persons(journeys)
    overlap_order_ids = {
        oid
        for person in overlap_users
        for oid in journeys[person].order_ids
    }
    overlap_gross = sum((order_index[oid].paid for oid in overlap_order_ids), ZERO)
    overlap_net = sum((order_index[oid].net for oid in overlap_order_ids), ZERO)
    overlap = {
        "persons": overlap_users,
        "order_ids": sorted(overlap_order_ids),
        "order_count": len(overlap_order_ids),
        "gross": overlap_gross,
        "net": overlap_net,
    }

    # ---- 平台汇总对账 --------------------------------------------------
    claims: list[PlatformClaim] = []
    orders_by_post: dict[str, int] = {}
    for order in dataset.orders:
        if order.post:
            orders_by_post[order.post] = orders_by_post.get(order.post, 0) + 1
    for signal in dataset.signals:
        spec = DEFAULT_METRICS.get(signal.metric)
        if spec is None:
            continue
        if spec.kind == "order_count":
            confirmed = Decimal(orders_by_post.get(signal.post, 0))
            note = (
                f"平台按 {signal.window or '未注明'} 窗口声称成交，订单表无窗口元数据；"
                "差额只展示不解释，两个来源不可相加"
            )
            claims.append(
                PlatformClaim(
                    record=signal.record,
                    post=signal.post,
                    metric=signal.metric,
                    window=signal.window,
                    platform_value=signal.value,
                    order_table_value=confirmed,
                    gap=signal.value - confirmed,
                    note=note,
                )
            )
        elif spec.kind == "impression":
            note = (
                f"{spec.description}；次数口径，禁止解释为独立顾客数，"
                "亦不能与订单数或金额相加"
            )
            claims.append(
                PlatformClaim(
                    record=signal.record,
                    post=signal.post,
                    metric=signal.metric,
                    window=signal.window,
                    platform_value=signal.value,
                    order_table_value=ZERO,
                    gap=signal.value,
                    note=note,
                )
            )
        elif spec.kind == "sales":
            confirmed_net = sum(
                (o.net for o in dataset.orders if o.post == signal.post), ZERO
            )
            claims.append(
                PlatformClaim(
                    record=signal.record,
                    post=signal.post,
                    metric=signal.metric,
                    window=signal.window,
                    platform_value=signal.value,
                    order_table_value=confirmed_net,
                    gap=signal.value - confirmed_net,
                    note="平台声称金额与订单表净销售口径不同，仅并列展示",
                )
            )

    return AttributionResult(
        fractions=fractions,
        model_totals=model_totals,
        post_totals=post_totals,
        unattributed=unattributed,
        unattributed_ids=unattributed_ids,
        confirmed_range=confirmed_range,
        upper_scenario=upper_scenario,
        upper_assumption=assumption,
        overlap=overlap,
        platform_claims=claims,
        totals=totals,
        creators=creators,
    )
