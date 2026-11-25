"""加载器：结构校验、硬错误与软告警。"""
import tempfile
import unittest
from pathlib import Path

from context import ROOT  # noqa: F401
from fixtures import write_dataset
from attribution.loading import InputError, load_dataset


class LoadingTests(unittest.TestCase):
    def test_loads_basic_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp)))
            self.assertEqual(len(dataset.contents), 3)
            self.assertEqual(len(dataset.signals), 3)
            self.assertEqual(len(dataset.orders), 3)
            self.assertEqual(len(dataset.contracts), 2)
            self.assertEqual(len(dataset.fingerprint), 64)
            self.assertEqual(dataset.orders[0].net, dataset.orders[0].paid)

    def test_missing_file_is_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(Path(tmp))
            (Path(tmp) / "orders.csv").unlink()
            with self.assertRaises(InputError):
                load_dataset(tmp)

    def test_bad_header_is_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(Path(tmp))
            (Path(tmp) / "contracts.csv").write_text(
                "creator,fixed,commission\nK1,100,0.1\n", encoding="utf-8"
            )
            with self.assertRaises(InputError):
                load_dataset(tmp)

    def test_bad_number_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(Path(tmp), orders="order_id,person,post,paid,refund,state\n"
                          "O1,U1,P1,abc,0,settled\n")
            with self.assertRaises(InputError):
                load_dataset(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(Path(tmp), contracts="creator,fixed,commission,period\n"
                          "K1,100,0.1,2026-10\nK1,200,0.2,2026-10\n")
            with self.assertRaises(InputError):
                load_dataset(tmp)

    def test_unknown_metric_and_channel_warn_but_load(self):
        signals = ("record,post,metric,value,window\n"
                   "S1,P1,unknown-metric,9,7d\n"
                   "S2,P2,platform-orders,44,14d\n"
                   "S3,P3,visits,120,30d\n")
        content = ("post,creator,channel,campaign,published\n"
                   "P1,K1,hologram,C1,2026-10-01\n"
                   "P2,K2,live,C1,2026-10-04\n"
                   "P3,K1,store-event,C2,2026-10-08\n")
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), content=content, signals=signals))
            self.assertTrue(any("unknown-metric" in w for w in dataset.warnings))
            self.assertTrue(any("hologram" in w for w in dataset.warnings))

    def test_dangling_post_in_order_degrades_to_unattributed(self):
        orders = ("order_id,person,post,paid,refund,state\n"
                  "O9,U9,PX,100,0,settled\n")
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), orders=orders))
            self.assertIsNone(dataset.orders[0].post)
            self.assertTrue(any("PX" in w for w in dataset.warnings))

    def test_refund_over_paid_warns_and_net_floored(self):
        orders = ("order_id,person,post,paid,refund,state\n"
                  "O1,U1,P1,100,130,settled\n")
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), orders=orders))
            self.assertEqual(dataset.orders[0].net, 0)
            self.assertTrue(any("退款" in w for w in dataset.warnings))

    def test_missing_contract_warning_keeps_creator(self):
        contracts = "creator,fixed,commission,period\nK2,12000,0.12,2026-10\n"
        with tempfile.TemporaryDirectory() as tmp:
            dataset = load_dataset(write_dataset(Path(tmp), contracts=contracts))
            self.assertTrue(any("K1" in w and "合同" in w for w in dataset.warnings))

    def test_fingerprint_changes_when_input_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp))
            first = load_dataset(path).fingerprint
            (path / "orders.csv").write_text(
                (path / "orders.csv").read_text(encoding="utf-8").replace(
                    "220", "230"
                ),
                encoding="utf-8",
            )
            second = load_dataset(path).fingerprint
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
