"""滞后窗与财务结算测试。"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from context import ROOT  # noqa: F401
from fixtures import ORDERS_BASE, ORDERS_WITH_DATES, write_dataset
from attribution.attribution import attribute
from attribution.finance import build_finance
from attribution.journeys import build_journeys
from attribution.loading import load_dataset
from attribution.timing import lag_report


class TimingTests(unittest.TestCase):
    def test_not_computable_without_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), orders=ORDERS_BASE))
            report = lag_report(dataset)
            self.assertFalse(report.computable)
            self.assertEqual(len(report.missing_date_orders), 3)

    def test_lag_buckets_and_negative_lag(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), orders=ORDERS_WITH_DATES))
            report = lag_report(dataset)
            self.assertTrue(report.computable)
            by_window = {b.window_days: b for b in report.buckets}
            # O1 滞后 2 天、O2 滞后 16 天、O4 滞后 55 天、O6 滞后 -1 天（异常但仍入窗）
            self.assertEqual(by_window[7].order_ids, ["O1", "O6"])
            self.assertEqual(by_window[30].order_ids, ["O1", "O2", "O6"])
            self.assertEqual(by_window[14].net, Decimal("300.00"))  # O1 220 + O6 80
            self.assertEqual(report.negative_lag_orders, ["O6"])
            self.assertIn("O3", report.missing_date_orders)
            self.assertEqual(report.lags["O2"], 16)
            self.assertEqual(report.lags["O4"], 55)


class FinanceTests(unittest.TestCase):
    def _build(self, orders: str):
        self.tmp = tempfile.TemporaryDirectory()
        dataset = load_dataset(write_dataset(Path(self.tmp.name), orders=orders))
        result = attribute(dataset, build_journeys(dataset))
        return dataset, result, build_finance(dataset, result)

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_commission_on_net_sales_and_settlement_split(self):
        orders = (
            "order_id,person,post,paid,refund,state\n"
            "O1,U1,P1,220,0,settled\n"
            "O2,U1,P2,180,45,settled\n"
            "O5,U3,P1,500,0,pending\n"
            "O3,U2,,150,0,unattributed\n"
        )
        dataset, result, finance = self._build(orders)
        k1, k2 = finance.by_creator["K1"], finance.by_creator["K2"]
        # K1：已结 O1 净 220 * 8% = 17.60；未结 O5 净 500 * 8% = 40.00
        self.assertEqual(k1.settled_commission, Decimal("17.60"))
        self.assertEqual(k1.unsettled_commission, Decimal("40.00"))
        self.assertEqual(k1.settled_order_ids, ["O1"])
        self.assertEqual(k1.unsettled_order_ids, ["O5"])
        # K2：O2 净 135 * 12% = 16.20
        self.assertEqual(k2.settled_commission, Decimal("16.20"))
        # 固定费用始终保留，已发生成本 = 固定 + 已结佣金（不含未结）
        self.assertEqual(k1.fixed, Decimal("8000.00"))
        self.assertEqual(k1.incurred_cost, Decimal("8017.60"))
        self.assertEqual(finance.total_fixed, Decimal("20000.00"))
        self.assertEqual(finance.total_unsettled_commission, Decimal("40.00"))

    def test_fixed_cost_survives_attribution_dispute(self):
        # 即使 K2 在首触模型下净销售为 0，固定费用 12000 仍在成本中
        orders = ORDERS_BASE
        dataset, result, finance = self._build(orders)
        self.assertEqual(result.model_totals["first_touch"]["K2"].net, Decimal("0.00"))
        self.assertEqual(finance.by_creator["K2"].fixed, Decimal("12000.00"))
        self.assertGreaterEqual(finance.by_creator["K2"].incurred_cost, Decimal("12000.00"))

    def test_missing_contract_zeroes_rate_and_warns(self):
        contracts = "creator,fixed,commission,period\nK2,12000,0.12,2026-10\n"
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), contracts=contracts))
            result = attribute(dataset, build_journeys(dataset))
            finance = build_finance(dataset, result)
            self.assertTrue(finance.by_creator["K1"].missing_contract)
            self.assertEqual(finance.by_creator["K1"].commission_rate, Decimal("0"))
            self.assertTrue(any("K1" in w for w in finance.warnings))

    def test_multi_period_contracts_sum_fixed_and_match_rate_by_month(self):
        contracts = (
            "creator,fixed,commission,period\n"
            "K1,1000,0.10,2026-10\n"
            "K1,1000,0.20,2026-11\n"
            "K2,12000,0.12,2026-10\n"
        )
        orders = (
            "order_id,person,post,paid,refund,state,paid_date\n"
            "O1,U1,P1,200,0,settled,2026-10-20\n"
            "O2,U2,P1,300,0,settled,2026-11-05\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), contracts=contracts, orders=orders))
            result = attribute(dataset, build_journeys(dataset))
            finance = build_finance(dataset, result)
            k1 = finance.by_creator["K1"]
            # 两份合同固定费用都保留：1000 + 1000
            self.assertEqual(k1.fixed, Decimal("2000.00"))
            # 10 月订单按 10%、11 月订单按 20%
            self.assertEqual(k1.settled_commission, Decimal("20.00") + Decimal("60.00"))
            self.assertEqual(k1.incurred_cost, Decimal("2080.00"))


if __name__ == "__main__":
    unittest.main()
