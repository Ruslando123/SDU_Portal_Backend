import unittest
from decimal import Decimal

from billing import attributed
from smoke import K6_SECONDS, SB_SECONDS, cost_plan, metrics_from
from worker import validate_origin


class SmokeSafetyTest(unittest.TestCase):
    def test_cost_uses_narrow_region_and_hard_resource_limits(self):
        rates = {"cpu_hour_cost": ".04730", "mem_gib_hour_cost": ".00800",
                 "cpu_hour_cost_sandbox": ".14190", "mem_gib_hour_cost_sandbox": ".02400",
                 "egress_gib_cost": ".04000"}
        cost = cost_plan(rates)
        self.assertAlmostEqual(cost["sandbox_compute_usd"], .0304325)
        self.assertLess(Decimal(str(cost["estimate_usd"])), Decimal("0.08"))
        self.assertIsNone(cost["actual_usd"])
        self.assertLessEqual(SB_SECONDS, 300)
        self.assertLessEqual(K6_SECONDS, 75)
        with self.assertRaises(ValueError):
            cost_plan({**rates, "cpu_hour_cost": "0"})

    def test_origin_disallows_external_or_ambiguous_targets(self):
        self.assertEqual(validate_origin("https://example.modal.host/"), "https://example.modal.host")
        for origin in ("https://example.com", "http://example.modal.host", "https://example.modal.host.bad",
                       "https://name@example.modal.host", "https://example.modal.host:8080",
                       "https://example.modal.host/x", "https://example.modal.host/?token=wrong"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_origin(origin)

    def test_billing_requires_an_exact_app_and_tag(self):
        report = {"id": "modal-20261107T114747Z-2d644d", "app_id": "ap-smoke"}
        row = {"object_id": "ap-smoke", "description": "sdu-phase8b-smoke", "resource": "CPU",
               "cost": "0.002", "tags": {"sdu_run": report["id"]}}
        other = {**row, "object_id": "ap-unrelated", "tags": {"sdu_run": "other"}}
        self.assertEqual(attributed([row, other], report), ("ap-smoke", {"CPU": Decimal("0.002")}))
        self.assertIsNone(attributed([row, {**row, "object_id": "ap-unrelated"}], report))
        self.assertIsNone(attributed([other], report))

    def test_absent_k6_summary_never_creates_zero_latency(self):
        result = metrics_from({"login_successes": 10})
        self.assertEqual(result["login_successes"], 10)
        self.assertIsNone(result["duration_seconds"])
        self.assertIsNone(result["rps"])
        self.assertIsNone(result["errors"])


if __name__ == "__main__":
    unittest.main()
