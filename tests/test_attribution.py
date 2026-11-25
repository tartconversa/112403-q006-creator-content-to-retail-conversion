"""旅程重建与归因模型数学。"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from context import ROOT  # noqa: F401
from fixtures import ORDERS_BASE, write_dataset
from attribution.attribution import MODELS, attribute
from attribution.journeys import build_journeys, unattributed_orders
from attribution.loading import load_dataset


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dataset = load_dataset(write_dataset(Path(self.tmp.name)))
        self.journeys = build_journeys(self.dataset)

    def tearDown(self):
        self.tmp.cleanup()

    def test_overlap_user_and_unattributed_pool(self):
        self.assertIn("U1", self.journeys)
        self.assertTrue(self.journeys["U1"].is_overlap)
        self.assertEqual(self.journeys["U1"].creators, ["K1", "K2"])
        pool = unattributed_orders(self.dataset)
        self.assertEqual([o.order_id for o in pool], ["O3"])

    def test_attribution_models_on_overlap(self):
        result = attribute(self.dataset, self.journeys)
        # 末触：O1->K1, O2->K2；首触：两单都给 K1
        self.assertEqual(result.model_totals["last_touch"]["K1"].net, Decimal("220.00"))
        self.assertEqual(result.model_totals["last_touch"]["K2"].net, Decimal("135.00"))
        self.assertEqual(result.model_totals["first_touch"]["K1"].net, Decimal("355.00"))
        self.assertEqual(result.model_totals["first_touch"]["K2"].net, Decimal("0.00"))
        # 均分：O2 净 135 由 K1/K2 平分，尾差规则保证合计守恒
        linear = result.model_totals["linear"]
        self.assertEqual(linear["K1"].net + linear["K2"].net, Decimal("355.00"))
        self.assertEqual(linear["K1"].net, Decimal("287.50"))
        self.assertEqual(linear["K2"].net, Decimal("67.50"))

    def test_every_order_allocated_exactly_once_per_model(self):
        result = attribute(self.dataset, self.journeys)
        for order_id, by_model in result.fractions.items():
            for model in MODELS:
                self.assertEqual(
                    sum(by_model[model].values()), Decimal("1"),
                    f"{order_id} {model} 分数合计必须为 1",
                )

    def test_conservation_across_models(self):
        result = attribute(self.dataset, self.journeys)
        for model in MODELS:
            creator_sum = sum(
                (result.model_totals[model][c].net for c in result.creators), Decimal("0")
            )
            self.assertEqual(
                creator_sum + result.unattributed.net, result.totals.net
            )

    def test_confirmed_range_and_upper_scenario(self):
        result = attribute(self.dataset, self.journeys)
        low, high = result.confirmed_range["K1"]
        self.assertEqual((low, high), (Decimal("220.00"), Decimal("355.00")))
        # 情景上限 = 确认净销售 + 无归属池分摊，且全达人上限增量合计等于无归属池
        extra = sum(
            (result.upper_scenario[c].net - result.model_totals["linear"][c].net
             for c in result.creators),
            Decimal("0"),
        )
        self.assertEqual(extra, result.unattributed.net)

    def test_post_totals_count_each_order_once(self):
        # 回归：帖子口径记整笔订单，不能按路径上的创作者重复累加
        result = attribute(self.dataset, self.journeys)
        for model in MODELS:
            post_net = sum(
                (amounts.net for amounts in result.post_totals[model].values()),
                Decimal("0"),
            )
            self.assertEqual(post_net, Decimal("355.00"))  # 仅可确认部分，不含无归属 O3
            self.assertEqual(result.post_totals[model]["P1"].order_count, 1)

    def test_platform_claims_never_merged_with_orders(self):
        result = attribute(self.dataset, self.journeys)
        claims = {c.record: c for c in result.platform_claims}
        # 平台声称 44 单 vs 订单表 1 单，差额展示但不相加
        self.assertEqual(claims["S2"].platform_value, Decimal("44"))
        self.assertEqual(claims["S2"].order_table_value, Decimal("1"))
        self.assertEqual(claims["S2"].gap, Decimal("43"))
        # 曝光次数口径订单表对照值必须为 0，不能换算成顾客
        self.assertEqual(claims["S1"].order_table_value, Decimal("0"))
        self.assertIn("独立顾客", claims["S1"].note)


if __name__ == "__main__":
    unittest.main()
