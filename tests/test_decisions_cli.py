"""决策快照不可变性、漂移检测与端到端 CLI。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from context import ROOT  # noqa: F401
from fixtures import write_dataset
from attribution.cli import main
from attribution.decisions import DecisionError, check_drift, load_decisions, sign_decision
from attribution.loading import load_dataset
from attribution.report import run_analysis


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = write_dataset(Path(self.tmp.name) / "data")
        self.store = Path(self.tmp.name) / "decisions"
        self.dataset = load_dataset(self.data)
        self.bundle = run_analysis(self.dataset)

    def tearDown(self):
        self.tmp.cleanup()

    def _evidence(self):
        amounts = self.bundle.attribution.model_totals["linear"]["K1"]
        return {"model": "linear", "net_sales": amounts.net}

    def test_snapshot_is_write_once(self):
        sign_decision(
            self.store, "D-001", "K1", "linear", self._evidence(),
            self.dataset.fingerprint, str(self.data),
        )
        with self.assertRaises(DecisionError):
            sign_decision(
                self.store, "D-001", "K1", "last_touch", self._evidence(),
                self.dataset.fingerprint, str(self.data),
            )
        path = self.store / "D-001.decision.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["immutable"])

    def test_drift_after_input_change_keeps_snapshot(self):
        sign_decision(
            self.store, "D-002", "K2", "last_touch", self._evidence(),
            self.dataset.fingerprint, str(self.data),
        )
        original = (self.store / "D-002.decision.json").read_text(encoding="utf-8")
        # 模拟平台补报：追加一行信号
        with (self.data / "signals.csv").open("a", encoding="utf-8") as handle:
            handle.write("S9,P1,views,500,7d\n")
        changed = load_dataset(self.data)
        statuses = check_drift(self.store, changed.fingerprint)
        self.assertEqual(len(statuses), 1)
        self.assertTrue(statuses[0].drifted)
        # 快照内容与文件权限保持签署时状态
        self.assertEqual(
            (self.store / "D-002.decision.json").read_text(encoding="utf-8"), original
        )

    def test_unsafe_decision_id_rejected(self):
        with self.assertRaises(DecisionError):
            sign_decision(
                self.store, "../evil", "K1", "linear", {},
                self.dataset.fingerprint, str(self.data),
            )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = write_dataset(Path(self.tmp.name) / "data")
        self.store = Path(self.tmp.name) / "decisions"

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_and_report(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["validate", str(self.data)])
        self.assertEqual(code, 0)
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["report", str(self.data)])
        self.assertEqual(code, 0)
        self.assertIn("合作复盘报告", out.getvalue())

    def test_json_report_is_strict_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["report", str(self.data), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())  # 严格解析，Infinity/NaN 会失败
        self.assertEqual(payload["schema_version"], 1)

    def test_sign_and_decisions_lifecycle(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([
                "sign", str(self.data), "D-100", "K1", "linear",
                "--store", str(self.store), "--signed-on", "2026-11-20",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(len(load_decisions(self.store)), 1)
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["decisions", str(self.data), "--store", str(self.store)])
        self.assertEqual(code, 0)
        self.assertIn("输入一致", out.getvalue())

    def test_hard_input_error_returns_exit_2(self):
        missing = Path(self.tmp.name) / "missing"
        missing.mkdir()
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["report", str(missing)])
        self.assertEqual(code, 2)
        self.assertIn("输入错误", err.getvalue())


if __name__ == "__main__":
    unittest.main()
