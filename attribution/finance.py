"""合同费用与佣金结算。

不可动摇的规则：合同约定的固定费用是已发生成本，即使转化归因存在争议，
也只能在不同归因解释下重算佣金，固定费用不会从成本中消失。

佣金只基于订单表中可确认到该达人内容的订单净销售（退款后），按合同费率
逐单计算；平台汇总声称的成交不产生佣金，因为没有可结算的订单身份。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .attribution import AttributionResult
from .models import TWO_PLACES, ZERO, Contract, Dataset


@dataclass
class CreatorFinance:
    creator: str
    contract: Contract | None
    fixed: Decimal
    commission_rate: Decimal
    period: str
    settled_net: Decimal = ZERO          # 已结算订单净销售
    unsettled_net: Decimal = ZERO        # 待结算订单净销售
    settled_commission: Decimal = ZERO   # 已结算佣金（应付/已付）
    unsettled_commission: Decimal = ZERO  # 仍未结算的佣金（续约必须提示）
    settled_order_ids: list[str] = field(default_factory=list)
    unsettled_order_ids: list[str] = field(default_factory=list)
    missing_contract: bool = False

    @property
    def incurred_cost(self) -> Decimal:
        """已发生成本：固定费用 + 已结算佣金（争议归因也不能抹除）。"""
        return (self.fixed + self.settled_commission).quantize(TWO_PLACES)

    @property
    def total_commission(self) -> Decimal:
        return (self.settled_commission + self.unsettled_commission).quantize(TWO_PLACES)


@dataclass
class FinanceReport:
    by_creator: dict[str, CreatorFinance]
    total_fixed: Decimal
    total_settled_commission: Decimal
    total_unsettled_commission: Decimal
    total_incurred_cost: Decimal
    warnings: list[str]


def _contract_for(
    order, contracts_by_creator: dict[str, list[Contract]], creator: str,
    warnings: list[str],
) -> Contract | None:
    candidates = contracts_by_creator.get(creator, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    period = order.paid_date.strftime("%Y-%m") if order.paid_date else None
    if period is None:
        warnings.append(
            f"订单 {order.order_id} 缺付款日期且达人有多份合同，无法判定适用合同期"
        )
        return None
    for contract in candidates:
        if contract.period == period:
            return contract
    warnings.append(f"订单 {order.order_id} 的付款月份 {period} 没有匹配合同")
    return None


def build_finance(dataset: Dataset, attribution: AttributionResult) -> FinanceReport:
    content_index = dataset.content_by_post()
    contracts: dict[str, list[Contract]] = {}
    for contract in dataset.contracts:
        contracts.setdefault(contract.creator, []).append(contract)

    all_creators = sorted({c.creator for c in dataset.contents} | set(contracts))
    by_creator: dict[str, CreatorFinance] = {}
    for creator in all_creators:
        creator_contracts = contracts.get(creator, [])
        # 固定费用按该达人全部合同期累加：每一份已签合同的费用都不可消失
        fixed_total = sum((c.fixed for c in creator_contracts), ZERO).quantize(TWO_PLACES)
        contract = creator_contracts[0] if creator_contracts else None
        periods = ",".join(c.period for c in creator_contracts) if creator_contracts else "无合同"
        by_creator[creator] = CreatorFinance(
            creator=creator,
            contract=contract,
            fixed=fixed_total,
            # 默认费率取首份合同；逐单结算时按订单付款月份匹配具体合同
            commission_rate=contract.commission_rate if contract else ZERO,
            period=periods,
            missing_contract=contract is None,
        )

    warnings: list[str] = []
    for order in dataset.orders:
        if order.post is None:
            continue
        creator = content_index[order.post].creator
        if creator not in by_creator:  # 数据异常兜底，正常加载阶段已告警
            continue
        finance = by_creator[creator]
        contract = _contract_for(order, contracts, creator, warnings)
        rate = contract.commission_rate if contract else finance.commission_rate
        commission = (order.net * rate).quantize(TWO_PLACES)
        if order.state == "settled":
            finance.settled_net += order.net
            finance.settled_commission += commission
            finance.settled_order_ids.append(order.order_id)
        else:
            finance.unsettled_net += order.net
            finance.unsettled_commission += commission
            finance.unsettled_order_ids.append(order.order_id)

    for finance in by_creator.values():
        finance.settled_net = finance.settled_net.quantize(TWO_PLACES)
        finance.unsettled_net = finance.unsettled_net.quantize(TWO_PLACES)
        finance.settled_commission = finance.settled_commission.quantize(TWO_PLACES)
        finance.unsettled_commission = finance.unsettled_commission.quantize(TWO_PLACES)
        if finance.missing_contract:
            warnings.append(
                f"达人 {finance.creator} 无合同：其订单佣金 {finance.total_commission} 元无法结算"
            )

    total_fixed = sum((f.fixed for f in by_creator.values()), ZERO)
    total_settled = sum((f.settled_commission for f in by_creator.values()), ZERO)
    total_unsettled = sum((f.unsettled_commission for f in by_creator.values()), ZERO)
    return FinanceReport(
        by_creator=by_creator,
        total_fixed=total_fixed.quantize(TWO_PLACES),
        total_settled_commission=total_settled.quantize(TWO_PLACES),
        total_unsettled_commission=total_unsettled.quantize(TWO_PLACES),
        total_incurred_cost=(total_fixed + total_settled).quantize(TWO_PLACES),
        warnings=warnings,
    )
