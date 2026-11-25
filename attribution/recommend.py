"""续约/待签约建议。

建议必须同时呈现三种归因解释和确认区间，不能只给单一数字；
待签约建议是派生结果，平台补报或退款后随输入重算而更新，
已签合作则由 :mod:`attribution.decisions` 固化为不可变快照。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .attribution import MODEL_LABELS, MODELS, AttributionResult
from .config import ROI_RENEW, ROI_REVIEW
from .finance import CreatorFinance, FinanceReport
from .models import TWO_PLACES, ZERO

RENEW_LABEL = "建议续约"
REVIEW_LABEL = "条件性续约/重新谈判"
CAUTION_LABEL = "暂不建议续约"


@dataclass
class CreatorRecommendation:
    creator: str
    tier: str
    roi_by_model: dict[str, Decimal]
    net_by_model: dict[str, Decimal]
    confirmed_low: Decimal
    confirmed_high: Decimal
    upper_net: Decimal
    incurred_cost: Decimal
    unsettled_commission: Decimal
    unsettled_order_ids: list[str]
    data_caveats: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class RecommendationReport:
    recommendations: list[CreatorRecommendation]
    unattributed_net: Decimal
    unattributed_order_ids: list[str]
    upper_assumption: str
    model_labels: dict[str, str]


def _tier(roi_values: list[Decimal]) -> str:
    # 以最不利归因解释下的 ROI 分级，避免乐观模型掩盖争议
    worst = min(roi_values)
    if worst >= ROI_RENEW:
        return RENEW_LABEL
    if worst >= ROI_REVIEW:
        return REVIEW_LABEL
    return CAUTION_LABEL


def build_recommendations(
    attribution: AttributionResult, finance: FinanceReport
) -> RecommendationReport:
    recs: list[CreatorRecommendation] = []
    for creator in attribution.creators:
        creator_finance: CreatorFinance = finance.by_creator[creator]
        cost = creator_finance.incurred_cost
        net_by_model: dict[str, Decimal] = {}
        roi_by_model: dict[str, Decimal] = {}
        for model in MODELS:
            net = attribution.model_totals[model][creator].net.quantize(TWO_PLACES)
            net_by_model[model] = net
            roi = (net / cost).quantize(TWO_PLACES) if cost > 0 else None
            roi_by_model[model] = roi if roi is not None else Decimal("Infinity")
        low, high = attribution.confirmed_range[creator]
        upper = attribution.upper_scenario[creator].net
        caveats: list[str] = []
        if creator_finance.missing_contract:
            caveats.append("缺少合同，佣金无法结算，建议先补签再评估续约")
        if creator_finance.unsettled_order_ids:
            caveats.append(
                f"仍有 {len(creator_finance.unsettled_order_ids)} 笔订单未结算，"
                f"未结算佣金 {creator_finance.unsettled_commission} 元"
            )
        if low == ZERO and high == ZERO:
            caveats.append("订单表中没有可确认到该达人的成交，效果只能参考平台汇总口径")
        finite_roi = [v for v in roi_by_model.values() if v.is_finite()]
        if not finite_roi:
            # 无合同/零成本时 ROI 不可计算，不能伪装成高 ROI 直接续约
            tier = REVIEW_LABEL
        else:
            tier = _tier(finite_roi)
        rationale = (
            f"已发生成本 {cost} 元（固定 {creator_finance.fixed} + 已结佣金 "
            f"{creator_finance.settled_commission}）；最不利解释下净销售 "
            f"{min(net_by_model.values())} 元，确认区间 {low}–{high} 元"
        )
        recs.append(
            CreatorRecommendation(
                creator=creator,
                tier=tier,
                roi_by_model=roi_by_model,
                net_by_model=net_by_model,
                confirmed_low=low,
                confirmed_high=high,
                upper_net=upper,
                incurred_cost=cost,
                unsettled_commission=creator_finance.unsettled_commission,
                unsettled_order_ids=list(creator_finance.unsettled_order_ids),
                data_caveats=caveats,
                rationale=rationale,
            )
        )
    return RecommendationReport(
        recommendations=recs,
        unattributed_net=attribution.unattributed.net,
        unattributed_order_ids=list(attribution.unattributed_ids),
        upper_assumption=attribution.upper_assumption,
        model_labels=MODEL_LABELS,
    )
