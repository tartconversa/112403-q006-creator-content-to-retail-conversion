"""在临时目录生成测试用 CSV，支持 paid_date 与各种异常场景。"""
from __future__ import annotations

from pathlib import Path

CONTENT = (
    "post,creator,channel,campaign,published\n"
    "P1,K1,short-video,C1,2026-10-01\n"
    "P2,K2,live,C1,2026-10-04\n"
    "P3,K1,store-event,C2,2026-10-08\n"
)
SIGNALS = (
    "record,post,metric,value,window\n"
    "S1,P1,views,18000,7d\n"
    "S2,P2,platform-orders,44,14d\n"
    "S3,P3,visits,120,30d\n"
)
CONTRACTS = (
    "creator,fixed,commission,period\n"
    "K1,8000,0.08,2026-10\n"
    "K2,12000,0.12,2026-10\n"
)
# O1/O2 属于同一用户 U1（触点重叠）；O3 无触点；O5 未结算
ORDERS_BASE = (
    "order_id,person,post,paid,refund,state\n"
    "O1,U1,P1,220,0,settled\n"
    "O2,U1,P2,180,45,settled\n"
    "O3,U2,,150,0,unattributed\n"
)
ORDERS_WITH_DATES = (
    "order_id,person,post,paid,refund,state,paid_date\n"
    "O1,U1,P1,220,0,settled,2026-10-03\n"
    "O2,U1,P2,180,45,settled,2026-10-20\n"
    "O3,U2,,150,0,unattributed,2026-10-15\n"
    "O4,U3,P1,500,0,pending,2026-11-25\n"
    # O6 付款早于 P2 发布日（2026-10-04），构造负滞后异常
    "O6,U4,P2,80,0,settled,2026-10-03\n"
)


def write_dataset(
    directory: Path,
    content: str = CONTENT,
    signals: str = SIGNALS,
    contracts: str = CONTRACTS,
    orders: str = ORDERS_BASE,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in {
        "content.csv": content,
        "signals.csv": signals,
        "contracts.csv": contracts,
        "orders.csv": orders,
    }.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory
