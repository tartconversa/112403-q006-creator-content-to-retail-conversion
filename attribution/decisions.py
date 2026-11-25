"""已签合作决策的证据快照（写入后不可变）。

业务规则：后续平台补报或订单退款会更新待签约建议，但已经做出的合作决策
必须保留当时采用的证据。快照一经写入即只读：同 ID 重签被拒绝，
输入指纹变化只做漂移提示，绝不回写旧快照。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .models import JsonEncoder

SNAPSHOT_SUFFIX = ".decision.json"


@dataclass
class SignedDecision:
    decision_id: str
    creator: str
    signed_on: str
    chosen_model: str
    evidence: dict[str, Any]
    input_fingerprint: str
    source_directory: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "creator": self.creator,
            "signed_on": self.signed_on,
            "chosen_model": self.chosen_model,
            "evidence": self.evidence,
            "input_fingerprint": self.input_fingerprint,
            "source_directory": self.source_directory,
            "note": self.note,
            "immutable": True,
        }


@dataclass
class DriftStatus:
    decision_id: str
    creator: str
    signed_on: str
    input_fingerprint: str
    current_fingerprint: str
    drifted: bool


class DecisionError(RuntimeError):
    """快照写入冲突或目录问题。"""


def _path(store: Path, decision_id: str) -> Path:
    safe = "".join(ch for ch in decision_id if ch.isalnum() or ch in ("-", "_"))
    if not safe or safe != decision_id:
        raise DecisionError(f"决策 ID 仅允许字母数字、连字符、下划线：{decision_id}")
    return store / f"{safe}{SNAPSHOT_SUFFIX}"


def sign_decision(
    store: str | Path,
    decision_id: str,
    creator: str,
    chosen_model: str,
    evidence: dict[str, Any],
    input_fingerprint: str,
    source_directory: str,
    signed_on: Optional[str] = None,
    note: str = "",
) -> SignedDecision:
    """写入一份只读决策快照；同 ID 已存在则拒绝。"""
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    target = _path(store, decision_id)
    if target.exists():
        raise DecisionError(
            f"决策 {decision_id} 已签署且不可变；新决策请使用新的 decision_id"
        )
    snapshot = SignedDecision(
        decision_id=decision_id,
        creator=creator,
        signed_on=signed_on or date.today().isoformat(),
        chosen_model=chosen_model,
        evidence=evidence,
        input_fingerprint=input_fingerprint,
        source_directory=source_directory,
        note=note,
    )
    target.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, cls=JsonEncoder),
        encoding="utf-8",
    )
    target.chmod(0o444)
    return snapshot


def load_decisions(store: str | Path) -> list[SignedDecision]:
    store = Path(store)
    if not store.exists():
        return []
    snapshots: list[SignedDecision] = []
    for path in sorted(store.glob(f"*{SNAPSHOT_SUFFIX}")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshots.append(
            SignedDecision(
                decision_id=payload["decision_id"],
                creator=payload["creator"],
                signed_on=payload["signed_on"],
                chosen_model=payload["chosen_model"],
                evidence=payload["evidence"],
                input_fingerprint=payload["input_fingerprint"],
                source_directory=payload["source_directory"],
                note=payload.get("note", ""),
            )
        )
    return snapshots


def check_drift(store: str | Path, current_fingerprint: str) -> list[DriftStatus]:
    """对照当前输入指纹检查每份已签快照（平台补报/退款后会显示漂移）。"""
    statuses: list[DriftStatus] = []
    for snapshot in load_decisions(store):
        statuses.append(
            DriftStatus(
                decision_id=snapshot.decision_id,
                creator=snapshot.creator,
                signed_on=snapshot.signed_on,
                input_fingerprint=snapshot.input_fingerprint,
                current_fingerprint=current_fingerprint,
                drifted=snapshot.input_fingerprint != current_fingerprint,
            )
        )
    return statuses
