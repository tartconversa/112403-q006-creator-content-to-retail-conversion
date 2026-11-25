"""命令行入口。

子命令：
  validate  仅校验输入并打印质量告警
  report    输出完整复盘报告（默认文本，--json 输出机器可读 JSON）
  sign      把一次合作决策固化为只读证据快照
  decisions 列出已签快照并检查输入漂移
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .attribution import MODELS
from .decisions import DecisionError, load_decisions, sign_decision
from .loading import InputError, load_dataset
from .report import render_json, render_text, run_analysis


def _print_warnings(warnings: list[str], stream) -> None:
    for warning in warnings:
        print(f"[告警] {warning}", file=stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attribution",
        description="达人内容长短链路评估与合作续约依据（标准库实现）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验输入文件结构与引用")
    p_validate.add_argument("input_dir", help="包含四份 CSV 的目录")

    p_report = sub.add_parser("report", help="生成合作复盘报告")
    p_report.add_argument("input_dir", help="包含四份 CSV 的目录")
    p_report.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    p_report.add_argument(
        "--decisions", default=None, help="已签决策快照目录（用于漂移检查）"
    )
    p_report.add_argument("-o", "--output", default=None, help="输出文件，默认标准输出")

    p_sign = sub.add_parser("sign", help="签署合作决策并写入只读快照")
    p_sign.add_argument("input_dir", help="决策所依据的输入目录")
    p_sign.add_argument("decision_id", help="决策 ID（字母数字、-、_）")
    p_sign.add_argument("creator", help="达人名")
    p_sign.add_argument("model", choices=MODELS, help="签署时采用的归因解释")
    p_sign.add_argument("--store", required=True, help="快照存放目录")
    p_sign.add_argument("--note", default="", help="决策备注")
    p_sign.add_argument("--signed-on", default=None, help="签署日期 YYYY-MM-DD")

    p_list = sub.add_parser("decisions", help="列出已签决策并检查输入漂移")
    p_list.add_argument("input_dir", help="当前输入目录（用于指纹比对）")
    p_list.add_argument("--store", required=True, help="快照存放目录")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset = load_dataset(args.input_dir)
    except InputError as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        if dataset.warnings:
            _print_warnings(dataset.warnings, sys.stderr)
        print(
            f"校验通过：{len(dataset.contents)} 条内容、{len(dataset.signals)} 条信号、"
            f"{len(dataset.orders)} 笔订单、{len(dataset.contracts)} 份合同"
        )
        return 0

    if args.command == "report":
        bundle = run_analysis(dataset, args.decisions)
        text = render_json(bundle) if args.json else render_text(bundle)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        all_warnings = list(dataset.warnings) + list(bundle.finance.warnings)
        if all_warnings:
            _print_warnings(all_warnings, sys.stderr)
        return 0

    if args.command == "sign":
        bundle = run_analysis(dataset)
        if args.creator not in bundle.attribution.creators:
            print(f"[输入错误] 达人 {args.creator} 不在输入数据中", file=sys.stderr)
            return 2
        amounts = bundle.attribution.model_totals[args.model][args.creator]
        creator_finance = bundle.finance.by_creator.get(args.creator)
        evidence = {
            "model": args.model,
            "net_sales": amounts.net,
            "gross_sales": amounts.gross,
            "refund": amounts.refund,
            "touched_order_count": amounts.order_count,
            "confirmed_range": {
                "low": bundle.attribution.confirmed_range[args.creator][0],
                "high": bundle.attribution.confirmed_range[args.creator][1],
            },
            "unattributed_pool_net": bundle.attribution.unattributed.net,
            "incurred_cost": creator_finance.incurred_cost if creator_finance else None,
            "unsettled_commission": (
                creator_finance.unsettled_commission if creator_finance else None
            ),
            "warnings_at_signing": list(dataset.warnings) + list(bundle.finance.warnings),
        }
        try:
            snapshot = sign_decision(
                store=args.store,
                decision_id=args.decision_id,
                creator=args.creator,
                chosen_model=args.model,
                evidence=evidence,
                input_fingerprint=dataset.fingerprint,
                source_directory=str(Path(args.input_dir).resolve()),
                signed_on=args.signed_on,
                note=args.note,
            )
        except DecisionError as exc:
            print(f"[决策错误] {exc}", file=sys.stderr)
            return 2
        print(f"已签署只读决策快照：{args.decision_id}（{snapshot.creator}）")
        return 0

    if args.command == "decisions":
        from .decisions import check_drift

        snapshots = load_decisions(args.store)
        if not snapshots:
            print("暂无已签决策")
            return 0
        for status in check_drift(args.store, dataset.fingerprint):
            flag = "输入已变化-快照保留" if status.drifted else "输入一致"
            print(
                f"{status.decision_id}\t{status.creator}\t{status.signed_on}\t{flag}"
            )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
