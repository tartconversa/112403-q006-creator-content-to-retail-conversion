"""复盘报告组装与渲染（纯文本 / JSON）。

文本面向品牌经理与合作复盘会，JSON 面向系统对接；两种输出共享同一份
分析结果，保证口径一致。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .attribution import MODEL_LABELS, MODELS, AttributionResult, attribute
from .decisions import DriftStatus
from .finance import FinanceReport, build_finance
from .journeys import build_journeys
from .loading import Dataset
from .models import TWO_PLACES, JsonEncoder, pad
from .recommend import RecommendationReport, build_recommendations
from .timing import LagReport, lag_report


@dataclass
class AnalysisBundle:
    dataset: Dataset
    attribution: AttributionResult
    finance: FinanceReport
    recommendation: RecommendationReport
    timing: LagReport
    drift: list[DriftStatus]


def run_analysis(dataset: Dataset, decision_store: Optional[str | Path] = None) -> AnalysisBundle:
    from .decisions import check_drift

    journeys = build_journeys(dataset)
    attribution = attribute(dataset, journeys)
    finance = build_finance(dataset, attribution)
    recommendation = build_recommendations(attribution, finance)
    timing = lag_report(dataset)
    drift = check_drift(decision_store, dataset.fingerprint) if decision_store else []
    return AnalysisBundle(
        dataset=dataset,
        attribution=attribution,
        finance=finance,
        recommendation=recommendation,
        timing=timing,
        drift=drift,
    )


def _fmt(value: Decimal) -> str:
    return f"{value.quantize(TWO_PLACES):,.2f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = []
    for i, header in enumerate(headers):
        column = [pad(header, 0)] + [pad(row[i], 0) for row in rows]
        widths.append(max(len(cell) for cell in column))
    line = "  ".join(pad(header, widths[i]) for i, header in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(pad(cell, widths[i]) for i, cell in enumerate(row)) for row in rows]
    return "\n".join([line, sep, *body])


def render_text(bundle: AnalysisBundle) -> str:
    dataset = bundle.dataset
    attribution = bundle.attribution
    finance = bundle.finance
    rec = bundle.recommendation
    timing = bundle.timing
    lines: list[str] = []

    lines.append("达人内容长短链路合作复盘报告")
    lines.append("=" * 34)
    lines.append(
        f"订单 {attribution.totals.order_count} 笔；付款合计 {_fmt(attribution.totals.gross)} 元，"
        f"退款 {_fmt(attribution.totals.refund)} 元，净销售 {_fmt(attribution.totals.net)} 元"
    )
    lines.append("")

    # 数据质量告警
    lines.append("一、数据质量与口径告警（分析师/运营确认项）")
    lines.append("-" * 34)
    all_warnings = list(dataset.warnings) + list(finance.warnings)
    if all_warnings:
        lines.extend(f"- {item}" for item in all_warnings)
    else:
        lines.append("- 无")
    lines.append("")

    # 可确认与无法归属范围
    lines.append("二、可确认触点与无法归属范围")
    lines.append("-" * 34)
    lines.append(
        f"- 可确认归属订单 {len(attribution.fractions)} 笔；"
        f"无法归属订单 {attribution.unattributed.order_count} 笔"
        f"（{', '.join(attribution.unattributed_ids) or '无'}），"
        f"净销售 {_fmt(attribution.unattributed.net)} 元单列，不分给任何达人"
    )
    ov = attribution.overlap
    lines.append(
        f"- 触点重叠用户 {len(ov['persons'])} 个（{', '.join(ov['persons']) or '无'}），"
        f"涉及订单 {ov['order_count']} 笔、净销售 {_fmt(ov['net'])} 元；"
        "这些订单的首触与末触分属不同创作者，是归因争议来源"
    )
    lines.append("")

    # 三种归因解释
    lines.append("三、不同归因解释下的达人净销售（退款后，单位：元）")
    lines.append("-" * 34)
    headers = ["达人"] + [MODEL_LABELS[m] for m in MODELS] + ["确认区间(低-高)", "情景上限"]
    rows: list[list[str]] = []
    for creator in attribution.creators:
        low, high = attribution.confirmed_range[creator]
        rows.append(
            [creator]
            + [_fmt(attribution.model_totals[m][creator].net) for m in MODELS]
            + [f"{_fmt(low)} ~ {_fmt(high)}", _fmt(attribution.upper_scenario[creator].net)]
        )
    lines.append(_table(headers, rows))
    lines.append(f"- {attribution.upper_assumption}")
    lines.append("")

    # 平台汇总对账与可比性
    lines.append("四、平台汇总指标对账与可比性")
    lines.append("-" * 34)
    if attribution.platform_claims:
        claim_rows = [
            [
                claim.record,
                claim.post,
                claim.metric,
                claim.window or "未注明",
                _fmt(claim.platform_value),
                _fmt(claim.order_table_value),
                _fmt(claim.gap),
            ]
            for claim in attribution.platform_claims
        ]
        lines.append(
            _table(
                ["记录", "内容", "指标", "窗口", "平台值", "订单表", "差额"],
                claim_rows,
            )
        )
        for claim in attribution.platform_claims:
            lines.append(f"- {claim.record}：{claim.note}")
    else:
        lines.append("- 无已确认含义的平台指标")
    lines.append("")

    # 滞后窗
    lines.append("五、内容发布到购买的滞后窗")
    lines.append("-" * 34)
    if not timing.computable:
        lines.append(f"- {timing.reason}")
    else:
        lag_rows = [
            [
                f"≤{bucket.window_days}天",
                str(len(bucket.order_ids)),
                _fmt(bucket.gross),
                _fmt(bucket.refund),
                _fmt(bucket.net),
            ]
            for bucket in timing.buckets
        ]
        lines.append(_table(["滞后窗", "订单数", "付款", "退款", "净销售"], lag_rows))
        if timing.missing_date_orders:
            lines.append(f"- 缺日期未入窗订单：{', '.join(timing.missing_date_orders)}")
        if timing.negative_lag_orders:
            lines.append(
                f"- 付款早于发布的异常订单：{', '.join(timing.negative_lag_orders)}"
            )
    lines.append("")

    # 费用与佣金
    lines.append("六、合同费用与佣金结算（固定费用为已发生成本，不可抹除）")
    lines.append("-" * 34)
    fee_rows = []
    for creator, f in finance.by_creator.items():
        fee_rows.append(
            [
                creator,
                _fmt(f.fixed),
                f"{f.commission_rate}",
                _fmt(f.settled_commission),
                _fmt(f.unsettled_commission),
                _fmt(f.incurred_cost),
            ]
        )
    lines.append(
        _table(
            ["达人", "固定费用", "佣金率", "已结佣金", "未结佣金", "已发生成本"],
            fee_rows,
        )
    )
    lines.append(
        f"- 合计固定费用 {_fmt(finance.total_fixed)} 元；已结佣金 "
        f"{_fmt(finance.total_settled_commission)} 元；仍未结算佣金 "
        f"{_fmt(finance.total_unsettled_commission)} 元；已发生成本合计 "
        f"{_fmt(finance.total_incurred_cost)} 元"
    )
    lines.append("")

    # 续约建议
    lines.append("七、续约/待签约建议（随输入更新；已签决策见第八节）")
    lines.append("-" * 34)
    for item in rec.recommendations:
        lines.append(f"- {item.creator}：{item.tier}")
        model_text = "；".join(
            f"{MODEL_LABELS[m]} 净销售 {_fmt(item.net_by_model[m])} / ROI "
            + ("∞" if not item.roi_by_model[m].is_finite() else _fmt(item.roi_by_model[m]))
            for m in MODELS
        )
        lines.append(f"    {model_text}")
        lines.append(f"    {item.rationale}")
        for caveat in item.data_caveats:
            lines.append(f"    注意：{caveat}")
    lines.append(
        f"- 无法归属池净销售 {_fmt(rec.unattributed_net)} 元"
        f"（{', '.join(rec.unattributed_order_ids) or '无'}）；{rec.upper_assumption}"
    )
    lines.append("")

    # 已签决策与漂移
    lines.append("八、已签合作决策（只读快照，保留签署时证据）")
    lines.append("-" * 34)
    if not bundle.drift:
        lines.append("- 暂无已签决策")
    else:
        for status in bundle.drift:
            flag = "输入已变化（快照保留原证据，不回写）" if status.drifted else "输入未变化"
            lines.append(
                f"- {status.decision_id} / {status.creator}，签署于 {status.signed_on}：{flag}"
            )
    return "\n".join(lines)


def _amounts_to_dict(amounts: Any) -> dict[str, Any]:
    return {
        "gross": amounts.gross,
        "refund": amounts.refund,
        "net": amounts.net,
        "order_count": amounts.order_count,
        "credited_orders": amounts.credited_orders,
    }


def build_json(bundle: AnalysisBundle) -> dict[str, Any]:
    attribution = bundle.attribution
    finance = bundle.finance
    return {
        "schema_version": 1,
        "input_fingerprint": bundle.dataset.fingerprint,
        "warnings": list(bundle.dataset.warnings) + list(finance.warnings),
        "totals": _amounts_to_dict(attribution.totals),
        "unattributed": {
            **_amounts_to_dict(attribution.unattributed),
            "order_ids": attribution.unattributed_ids,
        },
        "models": {
            model: {
                "label": MODEL_LABELS[model],
                "by_creator": {
                    creator: _amounts_to_dict(attribution.model_totals[model][creator])
                    for creator in attribution.creators
                },
                "by_post": {
                    post: _amounts_to_dict(amounts)
                    for post, amounts in attribution.post_totals[model].items()
                },
            }
            for model in MODELS
        },
        "order_fractions": attribution.fractions,
        "confirmed_range": {
            creator: {"low": low, "high": high}
            for creator, (low, high) in attribution.confirmed_range.items()
        },
        "upper_scenario": {
            creator: _amounts_to_dict(amounts)
            for creator, amounts in attribution.upper_scenario.items()
        },
        "upper_assumption": attribution.upper_assumption,
        "overlap": attribution.overlap,
        "platform_claims": [asdict(claim) for claim in attribution.platform_claims],
        "lag_windows": {
            "computable": bundle.timing.computable,
            "reason": bundle.timing.reason,
            "buckets": [
                {
                    "window_days": bucket.window_days,
                    "order_ids": bucket.order_ids,
                    "gross": bucket.gross,
                    "refund": bucket.refund,
                    "net": bucket.net,
                }
                for bucket in bundle.timing.buckets
            ],
            "missing_date_orders": bundle.timing.missing_date_orders,
            "negative_lag_orders": bundle.timing.negative_lag_orders,
            "lags": bundle.timing.lags,
        },
        "finance": {
            "by_creator": {
                creator: {
                    "fixed": f.fixed,
                    "commission_rate": f.commission_rate,
                    "period": f.period,
                    "settled_net": f.settled_net,
                    "unsettled_net": f.unsettled_net,
                    "settled_commission": f.settled_commission,
                    "unsettled_commission": f.unsettled_commission,
                    "incurred_cost": f.incurred_cost,
                    "settled_order_ids": f.settled_order_ids,
                    "unsettled_order_ids": f.unsettled_order_ids,
                    "missing_contract": f.missing_contract,
                }
                for creator, f in finance.by_creator.items()
            },
            "total_fixed": finance.total_fixed,
            "total_settled_commission": finance.total_settled_commission,
            "total_unsettled_commission": finance.total_unsettled_commission,
            "total_incurred_cost": finance.total_incurred_cost,
        },
        "recommendations": [asdict(item) for item in bundle.recommendation.recommendations],
        "signed_decisions": [asdict(status) for status in bundle.drift],
    }


def render_json(bundle: AnalysisBundle) -> str:
    return json.dumps(build_json(bundle), ensure_ascii=False, indent=2, cls=JsonEncoder)
