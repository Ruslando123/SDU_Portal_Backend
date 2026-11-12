import unittest
import base64
import gzip
import hashlib
import json
import pickle
import random
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock, patch

from bench_config import Case, budget, first_suite
from bench_evidence import checkouts_per_api_request, client_failures, join_traces, phase_metrics, read_samples, resource_summary
from benchmark import (WORKER_RESULT_CHUNK, enforce_predecessor, generator_headroom, load_passed, prior_reservations, receive_worker_result,
                       resource_limits, session_predecessor_passed, sql_checks)
from benchmark_worker import verify_access_with_dns_retry, warmup_gate
from evidence_transport import checkpoint_worker, read_worker_artifact, wait_for_worker
from mixed_evidence import business_results, database_ledger, reconcile
from mixed_recovery import pending_journeys, recover
from pooler_evidence import pooler_summary
from replica_evidence import replica_summary
from resource_topology import http_hop_evidence
from split_topology import postgres_evidence, read_gzip_export

RATES = {"cpu_hour_cost": ".04730", "mem_gib_hour_cost": ".00800",
         "cpu_hour_cost_sandbox": ".14190", "mem_gib_hour_cost_sandbox": ".02400", "egress_gib_cost": ".04000"}


class BenchmarkTests(unittest.TestCase):
    def test_split_pool_counts_remain_separate_and_match_request_counter_window(self):
        lines = []
        for epoch, runtime, security in ((1000, 10, 12), (6000, 20, 32)):
            lines.append(f"T\t{epoch}")
            for name, limit, count in (("sdu-runtime", 20, runtime), ("sdu-security", 4, security)):
                lines.extend(f'M\thikaricp_connections_{metric}{{pool="{name}"}} {value}' for metric, value in (
                    ("max", limit), ("pending", 2), ("acquire_seconds_count", count), ("acquire_seconds_sum", count / 1000)))
        frames = read_samples("\n".join(lines))
        window = {"window_started_ms": 900, "window_ended_ms": 6500}
        data = resource_summary(frames, window)
        self.assertEqual(data["hikari_acquisition"]["count"], 30)
        self.assertEqual(data["hikari_pools"]["sdu-runtime"]["acquisition"]["count"], 10)
        self.assertEqual(data["hikari_pools"]["sdu-security"]["max_connections"], 4)
        self.assertEqual(checkouts_per_api_request(frames, window, [(900, 50), (5900, 70)]), 1.5)
        self.assertIsNone(checkouts_per_api_request(frames, window, [(6000, 70)]))

    def test_dns_failure_retries_reads_of_the_same_worker_and_artifact(self):
        call = Mock()
        call.get.side_effect = [TimeoutError(), socket.gaierror("lost DNS"), {"size": 3}]
        with patch("evidence_transport.time.sleep"):
            self.assertEqual(wait_for_worker(call, 30), {"size": 3})
        self.assertEqual(call.get.call_count, 3)

        def partial():
            yield b"a"
            raise socket.gaierror("artifact DNS failed after one chunk")

        volume = Mock()
        volume.read_file.side_effect = [partial(), iter([b"ab", b"c"])]
        manifest = {"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}
        with patch("evidence_transport.time.sleep"):
            self.assertEqual(read_worker_artifact(volume, manifest), b"abc")
        self.assertEqual(volume.read_file.call_count, 2)
        volume.read_file.side_effect = None
        volume.read_file.return_value = iter([b"bad"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            read_worker_artifact(volume, manifest)

    def test_committed_start_marker_prevents_replaying_the_worker(self):
        with TemporaryDirectory() as directory:
            volume = Mock()
            workload = Mock(return_value=b"abc")
            manifest = checkpoint_worker(volume, directory, workload)
            self.assertEqual((Path(directory) / "worker-result.pkl").read_bytes(), b"abc")
            self.assertEqual(json.loads((Path(directory) / "worker-result.json").read_bytes()), manifest)
            self.assertEqual(volume.commit.call_count, 2)
            with self.assertRaises(FileExistsError):
                checkpoint_worker(volume, directory, workload)
            workload.assert_called_once()

    def test_dns_retry_is_limited_to_gateway_preflight(self):
        with patch("benchmark_worker.verify_access", side_effect=[socket.gaierror("temporary"), ({"ready": 200}, True)]):
            with patch("benchmark_worker.time.sleep"):
                self.assertEqual(verify_access_with_dns_retry("origin", "", "key"),
                                 ({"ready": 200, "preflight_dns_retries": 1}, True))

    def test_predecessor_requires_full_http_sql_and_evidence_cleanup(self):
        report = {"status": "passed", "stage": "complete", "sql_invariants_passed": True,
                  "metrics": {"duration_seconds": 120, "observed_vus_peak": 2000, "errors": 0,
                              "all_unexpected": 0, "interrupted_iterations": 0, "p95_ms": 100, "p99_ms": 200},
                  "warmup_gate": {"passed": True}, "reconciliation": {"passed": True, "final_state_verified": True},
                  "cleanup": {"verified": True, "app_state": "stopped", "active_sandboxes": 0, "active_containers": 0},
                  "evidence_volume_cleanup_verified": True}
        self.assertTrue(session_predecessor_passed(report, 2000))
        for override in ({"evidence_volume_cleanup_verified": False}, {"reconciliation": {"final_state_verified": True}},
                         {"metrics": {**report["metrics"], "duration_seconds": 119}},
                         {"cleanup": {**report["cleanup"], "active_containers": 1}}):
            self.assertFalse(session_predecessor_passed({**report, **override}, 2000))

        def saved_run(vus, cpu, pool=16, *, volume_clean=True):
            return {**report, "metrics": {**report["metrics"], "observed_vus_peak": vus},
                    "workload": {"session_storage": "redis", "vus": vus, "pool_size": pool},
                    "resources": {"sandbox_cpu_limit_cores": cpu},
                    "artifact": dict.fromkeys(("jar_sha256", "gateway_sha256", "workload_sha256", "fixture_sha256"), "sha"),
                    "evidence_volume_cleanup_verified": volume_clean}

        with TemporaryDirectory() as temp, patch("benchmark.RUNS", Path(temp)), patch("benchmark.sha", return_value="sha"):
            run = Path(temp) / "modal-cpu-test"
            run.mkdir()
            saved = run / "report.json"
            shared = dict(session_storage="redis", sidecar_cpu_cores=16, sidecar_cpu_limit=16, pool_size=24,
                          jvm_threads=None, joined_academic_reads=False, hardware=None, section_read_experiment=None,
                          jdbc_security_pool=None)
            retest = Mock(vus=2000, **shared)
            next_tier = Mock(vus=2500, **shared)
            saved.write_text(json.dumps(saved_run(1500, 12)))
            enforce_predecessor(retest)
            saved.write_text(json.dumps(saved_run(2000, 12, 24)))
            with self.assertRaises(SystemExit):
                enforce_predecessor(next_tier)
            saved.write_text(json.dumps(saved_run(2000, 16, 24, volume_clean=False)))
            with self.assertRaises(SystemExit):
                enforce_predecessor(next_tier)
            saved.write_text(json.dumps(saved_run(2000, 16)))
            with self.assertRaises(SystemExit):
                enforce_predecessor(next_tier)
            saved.write_text(json.dumps(saved_run(2000, 16, 24)))
            enforce_predecessor(next_tier)
            joined = Mock(vus=2500, **{**shared, "jvm_threads": "platform", "hardware": "ha-one",
                                     "section_read_experiment": "joined", "tomcat_threads": 80})
            matched = saved_run(2000, 16, 24)
            matched["jvm_evidence"] = {"verified": True}
            matched["workload"].update(jvm_threads="platform", hardware="ha-one", tomcat_threads=80,
                                       section_read_experiment="baseline")
            saved.write_text(json.dumps(matched))
            with self.assertRaises(SystemExit):
                enforce_predecessor(joined)
            matched["workload"]["section_read_experiment"] = "joined"
            saved.write_text(json.dumps(matched))
            enforce_predecessor(joined)
            shared_pool = Mock(vus=2500, **{**shared, "jvm_threads": "platform", "hardware": "ha-one",
                                           "jdbc_security_pool": 0, "tomcat_threads": 80})
            matched["workload"].update(section_read_experiment=None, jdbc_security_pool=4)
            saved.write_text(json.dumps(matched))
            with self.assertRaises(SystemExit):
                enforce_predecessor(shared_pool)
            matched["workload"]["jdbc_security_pool"] = 0
            saved.write_text(json.dumps(matched))
            enforce_predecessor(shared_pool)

            split = Case("mixed", 2500, pool_size=24, transport="encrypted-tunnel", spread_start=True,
                         ramp_seconds=60, ramp_start_vus=1, prefill_pool=True, split_database=True,
                         resource_profile="a", private_db_sidecar=True, http_experiment="direct",
                         session_storage="redis", sidecar_cpu_cores=16, jvm_threads="platform",
                         hardware="ha-one", tomcat_threads=80, jdbc_security_pool=4)
            matched["workload"] = replace(split, vus=2000, jdbc_security_pool=0).report()
            saved.write_text(json.dumps(matched))
            with self.assertRaises(SystemExit):
                enforce_predecessor(split)
            self.assertEqual(enforce_predecessor(split, shared_pool_control=True), run.name)
            with self.assertRaises(ValueError):
                enforce_predecessor(replace(split, jdbc_security_pool=6), shared_pool_control=True)
            matched["workload"]["diagnose_mixed"] = True
            saved.write_text(json.dumps(matched))
            with self.assertRaises(SystemExit):
                enforce_predecessor(split, shared_pool_control=True)

    def test_filesystem_evidence_rejects_incomplete_exports(self):
        text = b"one durable operation\n"
        compressed = gzip.compress(text)
        sandbox = Mock()
        sandbox.filesystem.read_bytes.return_value = compressed
        self.assertEqual(read_gzip_export(sandbox, "/evidence", str(len(compressed)),
                                         limit_mib=1, label="Test evidence"), text.decode())
        sandbox.filesystem.read_bytes.return_value = compressed[:-1]
        with self.assertRaisesRegex(RuntimeError, "filesystem export was incomplete"):
            read_gzip_export(sandbox, "/evidence", str(len(compressed)), limit_mib=1, label="Test evidence")

    def test_filesystem_retries_only_the_failed_binary_page(self):
        payload = base64.b64encode(random.Random(1).randbytes(640 * 1024))
        compressed = gzip.compress(payload)
        page_size = 512 * 1024
        pages = {f"/evidence.part-{i // page_size:03d}": compressed[i:i + page_size]
                 for i in range(0, len(compressed), page_size)}
        attempts = []

        def read(path):
            if path.endswith("001") and path not in attempts:
                attempts.append(path)
                raise socket.gaierror("DNS failed during this page")
            attempts.append(path)
            return pages[path]

        sandbox = Mock()
        sandbox.filesystem.read_bytes.side_effect = read
        with patch("split_topology.execute", return_value=(0, "", "")), patch("evidence_transport.time.sleep"):
            self.assertEqual(read_gzip_export(sandbox, "/evidence", str(len(compressed)),
                                             limit_mib=1, label="Test evidence"), payload.decode())
        self.assertCountEqual(attempts, ["/evidence.part-000", "/evidence.part-001", "/evidence.part-001"])

    def test_session_comparison_levels_keep_the_same_budget_and_disallow_larger_runs(self):
        options = dict(pool_size=16, transport="encrypted-tunnel", split_database=True, prefill_pool=True,
                       ramp_seconds=60, ramp_start_vus=1, spread_start=True, resource_profile="a",
                       private_db_sidecar=True, http_experiment="direct")
        for vus in (1000, 1500, 2000, 2500):
            jdbc = Case("mixed", vus, session_storage="jdbc", **options)
            redis = Case("mixed", vus, session_storage="redis", **options)
            self.assertEqual(budget(RATES, 1, jdbc), budget(RATES, 1, redis))
            self.assertEqual(resource_limits(jdbc), resource_limits(redis))
            self.assertEqual((redis.students, redis.section_capacity), (5000, 252))
        with self.assertRaises(ValueError):
            Case("mixed", 5000, session_storage="redis", **options)
        with self.assertRaises(ValueError):
            Case("mixed", 1000, session_storage="redis", session_read_mode="autocommit", **options)
        standard = Case("mixed", 1500, session_storage="redis", **options)
        cpu12 = Case("mixed", 1500, session_storage="redis", sidecar_cpu_cores=12, **options)
        cpu16 = Case("mixed", 1500, session_storage="redis", sidecar_cpu_cores=16, **options)
        self.assertEqual(resource_limits(cpu12), {**resource_limits(standard), "sandbox_cpu_limit_cores": 12})
        self.assertEqual(resource_limits(cpu16), {**resource_limits(standard), "sandbox_cpu_limit_cores": 16})
        self.assertGreater(budget(RATES, 1, cpu12)["estimate_usd"], budget(RATES, 1, standard)["estimate_usd"])
        self.assertGreater(budget(RATES, 1, cpu16)["estimate_usd"], budget(RATES, 1, cpu12)["estimate_usd"])
        with self.assertRaises(ValueError):
            Case("mixed", 1500, session_storage="jdbc", sidecar_cpu_cores=16, **options)
        larger_pool = {**options, "pool_size": 24}
        for vus in (2000, 2500):
            case = Case("mixed", vus, session_storage="redis", sidecar_cpu_cores=16, **larger_pool)
            self.assertEqual((case.pool_min_idle, resource_limits(case)["backend_pool_size"]), (24, 24))
        with self.assertRaises(ValueError):
            Case("mixed", 2000, session_storage="redis", sidecar_cpu_cores=12, **larger_pool)
        phase12 = dict(session_storage="redis", sidecar_cpu_cores=16, jvm_threads="platform", **larger_pool)
        one = Case("mixed", 2000, hardware="ha-one", **phase12)
        two = Case("mixed", 2000, hardware="ha-two", **phase12)
        self.assertEqual(budget(RATES, 1, one), budget(RATES, 1, two))
        for arm in (one, two):
            limits = resource_limits(arm)
            self.assertEqual(limits["backend_pool_size"] * arm.app_processes, 24)
            self.assertEqual(limits["backend_jvm_max_heap_mib"] * arm.app_processes, 4096)
        for security in (0, 4, 6):
            arm = replace(one, tomcat_threads=80, jdbc_security_pool=security)
            limits = resource_limits(arm)
            self.assertEqual((limits["backend_pool_size"], limits["security_pool_size"]), (24 - security, security))
            self.assertEqual(budget(RATES, 1, one), budget(RATES, 1, arm))
        open_mixed = Case("mixed", 2000, arrival_rate=1400, **phase12).report()
        self.assertEqual((open_mixed["executor"], open_mixed["warmup_vus"], open_mixed["minimum_iteration_seconds"]),
                         ("constant-arrival-rate", 2000, None))
        with self.assertRaises(ValueError):
            Case("mixed", 2500, arrival_rate=1400, **phase12)
        pool_controls = [Case("mixed", 2500, **{**phase12, "pool_size": pool, "hardware": "ha-one", "tomcat_threads": threads})
                         for threads, pool in ((80, 24), (80, 32), (120, 24))]
        self.assertEqual({(case.sidecar_cpu_limit, case.sidecar_memory_limit) for case in pool_controls}, {(16, 17408)})
        self.assertEqual({budget(RATES, 1, case)["estimate_usd"] for case in pool_controls},
                         {budget(RATES, 1, pool_controls[0])["estimate_usd"]})
        with self.assertRaises(ValueError):
            Case("mixed", 2500, **{**phase12, "pool_size": 32, "hardware": "ha-two", "tomcat_threads": 80})
        self.assertIn("LIMIT 240001", database_ledger(240_000))

    def test_redis_metrics_preserve_zero_and_histogram_bounds(self):
        lines = []
        for t, values in ((1000, (1, 1, 1, 0)), (2000, (51, 96, 101, 0))):
            lines.append(f"T\t{t}")
            lines.append("M\tsdu_redis_used_memory 1048576")
            lines.append(f"M\tsdu_redis_evicted_keys {values[3]}")
            lines.append('M\tsession_redis_seconds_bucket{operation="read",le="0.001"} ' + str(values[0]))
            lines.append('M\tsession_redis_seconds_bucket{operation="read",le="0.002"} ' + str(values[1]))
            lines.append('M\tsession_redis_seconds_bucket{operation="read",le="0.003"} ' + str(values[2]))
            lines.append('M\tsession_redis_seconds_count{operation="read"} ' + str(values[2]))
            lines.append('M\tsession_redis_seconds_sum{operation="read"} ' + str(t / 1000))
        redis = resource_summary(read_samples("\n".join(lines)),
                                 {"window_started_ms": 1000, "window_ended_ms": 2000})["redis"]
        self.assertEqual(redis["evicted_keys"], 0)
        self.assertIsNone(redis["expired_keys"])
        self.assertEqual([redis["operations"]["read"][key] for key in ("count", "p50_ms", "p95_ms", "p99_ms")],
                         [100, 1, 2, 3])

    def test_ha_proxy_connect_timeout_is_distinct_from_backend_ingress(self):
        trace = "40000000-0000-4000-8000-0000000147bd"
        output = f"sdu_haproxy {trace} 503 sC-- 0 0 -1 -1 2001 0 0 0\ncount 503 sC-- 1\n"
        with patch("resource_topology.execute", side_effect=[(0, output, ""), (0, "", "")]):
            evidence = http_hop_evidence("edge", "node", [{"trace": trace}])
        self.assertEqual(evidence["status_termination_counts"], {"503 sC--": 1})
        self.assertEqual(evidence["haproxy"][trace]["connect_ms"], -1)
        self.assertEqual(evidence["haproxy"][trace]["total_ms"], 2001)
        self.assertEqual(evidence["matched_node_gateway"], 0)

    def test_private_warmup_gate_rejects_plateau_errors_and_missing_window(self):
        case = Case("mixed", 1000, pool_size=16, transport="encrypted-tunnel", split_database=True,
                    resource_profile="a", private_db_sidecar=True, ramp_seconds=60, ramp_start_vus=1,
                    prefill_pool=True, spread_start=True)
        metrics = {
            "vus": {"values": {"max": 1000}},
            "cloud_scenario_start_ms": {"values": {"value": 100000}},
            "cloud_window_requests": {"values": {"count": 36000}},
            "cloud_outcomes{outcome:success}": {"values": {"count": 36000}},
            "cloud_window_unexpected": {"values": {"count": 0}},
            "cloud_iterations_started": {"values": {"count": 40000}},
            "cloud_window_iterations_started": {"values": {"count": 23900}},
            "iterations": {"values": {"count": 40000}},
            "cloud_window_latency": {"values": {"count": 36000, "p(95)": 100, "p(99)": 200}},
        }
        state = {"testRunDurationMs": 90100}
        payload = {"exit": 0, "summary": {"state": state, "metrics": metrics}, "ledger": b"captured",
                   "recovery": {"pending": 0, "resolved": 0}}
        self.assertTrue(warmup_gate(case, payload)["passed"])
        metrics["cloud_window_unexpected"]["values"]["count"] = 1
        self.assertFalse(warmup_gate(case, payload)["checks"]["zero_unexpected"])
        metrics["cloud_window_unexpected"]["values"]["count"] = 0
        state["testRunDurationMs"] = 89000
        self.assertFalse(warmup_gate(case, payload)["checks"]["full_target_window"])

    def test_pool_diagnosis_can_observe_a_pace_only_miss_without_qualifying(self):
        case = Case("mixed", 2500, pool_size=24, transport="encrypted-tunnel", split_database=True,
                    resource_profile="a", private_db_sidecar=True, ramp_seconds=60, ramp_start_vus=1,
                    prefill_pool=True, spread_start=True, session_storage="redis", sidecar_cpu_cores=16,
                    jvm_threads="platform", hardware="ha-one", http_experiment="direct", tomcat_threads=80)
        metrics = {"duration_seconds": 30, "window_started_ms": 60000, "window_ended_ms": 90000,
                   "observed_vus_peak": 2500, "requests": 83291, "errors": 0, "all_unexpected": 0,
                   "interrupted_iterations": 0, "dropped_iterations": 0, "p95_ms": 757, "p99_ms": 860,
                   "offered_iterations": 56855}
        payload = {"exit": 0, "ledger": b"captured", "recovery": {"pending": 0, "resolved": 0}}
        with patch("benchmark_worker.phase_metrics", return_value=metrics):
            gate = warmup_gate(case, payload)
            self.assertTrue(gate["diagnostic_measurement_allowed"])
            self.assertFalse(load_passed(case, {}, {}, {"warmup_gate": gate}))
            candidate = replace(case, section_read_experiment="joined")
            self.assertFalse(warmup_gate(candidate, payload)["passed"])
            self.assertNotIn("diagnostic_measurement_allowed", warmup_gate(candidate, payload))
            split_pool = replace(case, jdbc_security_pool=4)
            self.assertFalse(warmup_gate(split_pool, payload)["passed"])
            self.assertNotIn("diagnostic_measurement_allowed", warmup_gate(split_pool, payload))
            metrics["all_unexpected"] = 1
            self.assertFalse(warmup_gate(case, payload)["diagnostic_measurement_allowed"])
            metrics["all_unexpected"] = 0
            metrics["offered_iterations"] = 53999
            self.assertFalse(warmup_gate(case, payload)["diagnostic_measurement_allowed"])

    def test_resource_profiles_keep_fixed_database_dataset_and_aggregate_pool(self):
        options = dict(pool_size=16, transport="encrypted-tunnel", split_database=True, prefill_pool=True,
                       ramp_seconds=60, ramp_start_vus=1, spread_start=True)
        cases = [Case("mixed", 200, resource_profile=profile, **options) for profile in ("a", "b", "c")]
        limits = [resource_limits(case) for case in cases]
        self.assertEqual([case.instances * case.sandbox_cpu for case in cases], [4, 4, 8])
        self.assertEqual([case.instances * case.sandbox_memory for case in cases], [8192, 8192, 16384])
        for case, allocated in zip(cases, limits, strict=True):
            self.assertEqual(allocated["database_cpu_limit_cores"], 4)
            self.assertEqual(allocated["database_memory_limit_mib"], 8192)
            self.assertEqual(allocated["backend_pool_size"] * case.instances, 16)
            self.assertEqual((case.students, case.section_capacity), (5000, 252))
        self.assertEqual(budget(RATES, 1, cases[0]), budget(RATES, 1, cases[1]))
        self.assertGreater(budget(RATES, 1, cases[2])["estimate_usd"], budget(RATES, 1, cases[0])["estimate_usd"])
        with self.assertRaises(ValueError):
            Case("mixed", 200, resource_profile="a", **{**options, "split_database": False})

    def test_budget_reserves_only_the_unmetered_ceiling_after_verified_cleanup(self):
        with TemporaryDirectory() as temp, patch("benchmark.RUNS", Path(temp)):
            path = Path(temp) / "modal-test"
            path.mkdir()
            report = dict(cost=dict(estimate_usd=1, actual_usd=0.4, actual_source="modal-billing-report"),
                          cleanup=dict(verified=False))
            (path / "report.json").write_text(json.dumps(report))
            self.assertEqual(prior_reservations(), Decimal("1"))
            report["cleanup"]["verified"] = True
            (path / "report.json").write_text(json.dumps(report))
            self.assertEqual(prior_reservations(), Decimal("0.6"))

    def test_private_sidecar_keeps_profile_a_fixture_and_aggregate_resources(self):
        options = dict(pool_size=16, transport="encrypted-tunnel", split_database=True, prefill_pool=True,
                       ramp_seconds=60, ramp_start_vus=1, spread_start=True)
        prior = Case("mixed", 200, resource_profile="a", **options)
        private = Case("mixed", 200, resource_profile="a", private_db_sidecar=True, **options)
        self.assertEqual((private.students, private.section_capacity, private.pool_size, private.generator_cpu),
                         (prior.students, prior.section_capacity, prior.pool_size, prior.generator_cpu))
        previous_limits = resource_limits(prior)
        current_limits = resource_limits(private)
        self.assertEqual(current_limits["sandbox_cpu_limit_cores"],
                         previous_limits["sandbox_cpu_limit_cores"] + previous_limits["database_cpu_limit_cores"])
        self.assertEqual(current_limits["sandbox_memory_limit_mib"],
                         previous_limits["sandbox_memory_limit_mib"] + previous_limits["database_memory_limit_mib"])
        self.assertNotIn("database_cpu_limit_cores", current_limits)
        private_500 = Case("mixed", 500, resource_profile="a", private_db_sidecar=True, **options)
        self.assertEqual(private.response_mib, 256)
        self.assertEqual(private_500.response_mib, 512)
        self.assertEqual(private_500.request_limit, Case("mixed", 500, resource_profile="a", **options).request_limit)
        private_1000 = Case("mixed", 1000, resource_profile="a", private_db_sidecar=True, **options)
        limits_500 = resource_limits(private_500)
        limits_1000 = resource_limits(private_1000)
        for key in ("sandbox_timeout_seconds", "generator_timeout_seconds"):
            self.assertEqual(limits_1000.pop(key), limits_500.pop(key) + 100)
        self.assertEqual(limits_1000, limits_500)
        self.assertEqual((private_1000.students, private_1000.section_capacity), (private_500.students, private_500.section_capacity))
        self.assertEqual((private_1000.request_limit, private_1000.response_mib), (285_000, 1_024))
        private_2500 = Case("mixed", 2500, resource_profile="a", private_db_sidecar=True, **options)
        self.assertTrue(private_2500.private_scaled)
        self.assertEqual((private_2500.students, private_2500.section_capacity, private_2500.generator_cpu), (5000, 252, 4))
        self.assertEqual((private_2500.request_limit, private_2500.response_mib), (712_500, 2_560))
        limits_2500 = resource_limits(private_2500)
        self.assertTrue(private_2500.private_loopback)
        self.assertEqual((limits_2500["sandbox_cpu_limit_cores"], limits_2500["sandbox_memory_limit_mib"]), (9, 17408))
        self.assertNotIn("edge_cpu_limit_cores", limits_2500)
        self.assertLess(budget(RATES, 1, private_2500)["estimate_usd"], Decimal("1.75"))
        route_a = Case("mixed", 2500, resource_profile="a", private_db_sidecar=True, http_experiment="haproxy",
                       diagnostic_only=True, **options)
        route_b = Case("mixed", 2500, resource_profile="a", private_db_sidecar=True, http_experiment="direct",
                       diagnostic_only=True, **options)
        self.assertEqual(resource_limits(route_a), resource_limits(route_b))
        self.assertEqual(route_a.ramp_elapsed, 90)
        self.assertEqual(route_b.sandbox_seconds, private_2500.sandbox_seconds - 90)
        with self.assertRaises(ValueError):
            Case("mixed", 200, resource_profile="b", private_db_sidecar=True, **options)
        with self.assertRaises(ValueError):
            Case("mixed", 5000, resource_profile="a", private_db_sidecar=True, **options)

    def test_replica_summary_scopes_catalog_counts_and_requires_streaming_recovery(self):
        def sample(time, primary_calls, replica_calls, state="streaming"):
            stream = dict(state=state, lag_bytes=time, replay_lag_ms=None)
            primary = json.dumps(dict(catalog_calls=primary_calls, stream=stream))
            replica = json.dumps(dict(catalog_calls=replica_calls, recovery=True))
            return f"{time}\t{primary}\t{replica}\n"

        raw = sample(1000, 0, 1) + sample(2000, 0, 3) + sample(3000, 0, 9) + sample(4000, 7, 9, "catchup")
        summary = replica_summary(raw, 1500, 4500, True)
        self.assertEqual(summary, dict(samples=2, primary_catalog_calls=0, replica_catalog_calls=6,
                                       max_lag_bytes=3000, max_replay_lag_ms=None))

    def test_pgbouncer_summary_uses_only_the_selected_window(self):
        def sample(time, waiting, queries):
            return (
                f"T\t{time}\nP\tdatabase,cl_active,cl_waiting,sv_active,sv_idle\n"
                f"P\tportal_cloud_smoke,16,{waiting},3,13\nP\tpgbouncer,1,0,0,0\n"
                f"S\tdatabase,total_query_count,total_bind_count\nS\tportal_cloud_smoke,{queries},100\n"
            )
        summary = pooler_summary(sample(1000, 99, 1) + sample(2000, 0, 3) + sample(3000, 2, 9), 1500, 3500)
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["clients_waiting_peak"], 2)
        self.assertEqual(summary["total_queries"], 6)
        self.assertIsNone(summary["cpu_seconds"])

    def test_business_throughput_excludes_post_test_recovery_and_counts_same_key_once(self):
        def attempt(trace, phase, started, ended, outcome, boundary=None):
            status = 201 if outcome == "success" else 0
            response = dict(trace=trace, route="enroll", started_ms=started, ended_ms=ended,
                            outcome=outcome, status=status, phase=phase, code="-", unexpected=status == 0)
            return dict(trace=trace, started_ms=started, response=response, committed_this_attempt=boundary is not None,
                        timeout_boundary=boundary)

        lost = attempt("lost", "measure", 1000, 1100, "timeout", "timeout_after_commit")
        replay = attempt("retry", "measure", 1100, 1140, "success")
        recovered = attempt("recovered", "measure-recovery", 2050, 2060, "success")
        outcome = {"claimed_ms": 1020, "commit_ms": 1080}
        evidence = {"operations": [
            {"operation": "ENROLL", "durable": outcome, "consistent": True, "attempts": [lost, replay]},
            {"operation": "ENROLL", "durable": {"claimed_ms": 2040, "commit_ms": 2050},
             "consistent": True, "attempts": [recovered]},
        ], "unexpected": [lost["response"]]}
        result = business_results(evidence, {}, {"duration_seconds": 1, "window_started_ms": 1000, "window_ended_ms": 2000})
        self.assertEqual(result["mutations"]["enroll"]["acknowledged"], 1)
        self.assertEqual(result["mutations"]["enroll"]["p95_ms"], 140)
        self.assertEqual(result["mutations"]["enroll"]["claim_to_commit"]["p99_ms"], 60)
        self.assertEqual(result["failures"][0]["transaction_evidence"], "timeout_after_commit")

    def test_restricted_generator_result_larger_than_inline_blob_limit(self):
        result = {"measure": {"ledger": bytes(range(256)) * 10_000}}
        payload = pickle.dumps(result)
        chunks = [payload[i:i + WORKER_RESULT_CHUNK] for i in range(0, len(payload), WORKER_RESULT_CHUNK)]
        self.assertEqual(receive_worker_result(chunks), result)
        with self.assertRaises(ValueError):
            receive_worker_result([payload])

    def test_unacknowledged_enrollment_replays_original_key_then_withdraws(self):
        principal = "81000000-0000-4000-8000-000000000001"
        section = "90000000-0000-4000-8000-000000000001"
        episode = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        key = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        trace = "20000000-0000-4000-8000-000000000001"
        ledger = gzip.compress((f"SDU_START {trace} enroll {key} 1 {principal} {section} 1000\n"
            f"SDU_RESULT {trace} enroll 0 timeout unattributed 1000 5000 - - 1 -").encode())
        actor = {"student": "80000000-0000-4000-8000-000000000001", "cookie": "synthetic", "header": "X-CSRF-TOKEN", "csrf": "fixture"}
        calls = []

        class Response:
            headers = {"X-Sdu-Benchmark-Hop": "backend"}

            def __init__(self, status, body):
                self.code, self.body = status, json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, count):
                return self.body[:count]

        class Client:
            def open(self, request, timeout):
                calls.append((request.method, request.full_url, request.get_header("Idempotency-key")))
                if request.method == "POST":
                    return Response(201, {"code": "ENROLLED", "enrollmentId": episode, "sectionId": section, "correlationId": trace})
                return Response(200, {"code": "WITHDRAWN", "enrollmentId": episode,
                                      "correlationId": request.get_header("X-correlation-id")})

        from mixed_evidence import parse_client_ledger
        recovered, summary = recover(ledger, [actor], "https://test.invalid", {}, Client, "measure")
        self.assertEqual(summary, {"pending": 1, "resolved": 1, "requests": 2, "replays": 0})
        self.assertEqual(calls[0], ("POST", f"https://test.invalid/api/v1/sections/{section}/enrollments", key))
        self.assertEqual(calls[1][:2], ("DELETE", f"https://test.invalid/api/v1/enrollments/{episode}"))
        self.assertNotEqual(calls[1][2], key)
        starts, results = parse_client_ledger(recovered)
        self.assertEqual(next(iter(starts.values()))["attempt"], 2)
        self.assertEqual({result["phase"] for result in results.values()}, {"measure-recovery"})

    def test_withdrawal_timeout_keeps_target_and_original_key(self):
        principal = "81000000-0000-4000-8000-000000000001"
        episode = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        key = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        first, retry = (f"20000000-0000-4000-8000-{index:012d}" for index in (1, 2))
        ledger = gzip.compress((f"SDU_START {first} withdraw {key} 1 {principal} {episode} 1000\n"
            f"SDU_RESULT {first} withdraw 0 timeout unattributed 1000 5000 - - 1 -\n"
            f"SDU_START {retry} withdraw {key} 2 {principal} {episode} 5000\n"
            f"SDU_RESULT {retry} withdraw 0 timeout unattributed 5000 9000 - - 1 -").encode())
        pending, = pending_journeys(ledger)
        self.assertEqual((pending["route"], pending["target"], pending["key"], pending["attempt"]),
                         ("withdraw", episode, key, 3))

    def test_postgres_evidence_uses_only_in_window_frames_and_wal_deltas(self):
        def frame(epoch, ticks, writes):
            pg = {"connections": 16, "active": 1, "waits": {"LWLock:WALWrite": 1}, "lock_edges": [],
                  "wal": {"reset": "stable", "bytes": writes * 100},
                  "wal_io": {"reset": "stable", "syncs": writes, "sync_ms": writes * 2}}
            return f"T\t{epoch}\nP\t{json.dumps(pg)}\nM\tsdu_pg_ticks_per_second 100\nM\tsdu_pg_page_size 4096\nO\t1 {ticks} 1024\n"
        raw = frame(900, 5, 1) + frame(1000, 10, 4) + frame(2000, 30, 7) + frame(3000, 500, 99)
        with TemporaryDirectory() as work, patch("split_topology.execute", side_effect=[(0, raw, ""), (0, "", "")]), \
                patch("split_topology.query", return_value="header"):
            evidence = postgres_evidence(None, None, Path(work), {"scenario_started_ms": 1000, "observed_window_ended_ms": 2000})
        self.assertEqual(evidence["postgres_samples"], 2)
        self.assertEqual(evidence["postgres_process_cpu_seconds"], .2)
        self.assertEqual(evidence["postgres_process_rss_sum_peak_mib"], 4)
        self.assertEqual(evidence["postgres_wal_window"], {"bytes": 300, "syncs": 3, "sync_ms": 6})
        self.assertEqual(evidence["postgres_wait_events"], {"LWLock:WALWrite": 1})

    def test_mixed_reconciliation_recovers_lost_commit_ack_with_same_key(self):
        principal = "81000000-0000-4000-8000-000000000001"
        target = "90000000-0000-4000-8000-000000000001"
        key = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        first, second = (f"20000000-0000-4000-8000-{index:012d}" for index in (1, 2))
        ledger = gzip.compress((f"SDU_START {first} enroll {key} 1 {principal} {target} 1000\n"
            f"SDU_RESULT {first} enroll 0 timeout unattributed 1000 5000 - - 1\n"
            f"SDU_START {second} enroll {key} 2 {principal} {target} 5010\n"
            f"SDU_RESULT {second} enroll 201 success backend 5010 5020 ENROLLED {first} 0\n").encode())
        durable = "\t".join((principal, "ENROLL", key, target, key, "COMPLETED", "201", "ENROLLED", first, key, "WITHDRAWN", "2123", "4300"))
        result = reconcile([ledger], durable)
        self.assertEqual(result["summary"]["retries"], 1)
        self.assertEqual(result["summary"]["inconsistent"], 0)
        self.assertEqual(result["operations"][0]["attempts"][0]["timeout_boundary"], "timeout_after_commit")
        self.assertEqual(result["operations"][0]["durable"]["final_enrollment_state"], "WITHDRAWN")
        self.assertEqual(result["operations"][0]["durable"]["transaction_xid"], "2123")
        self.assertEqual(reconcile([ledger], "")["summary"]["inconsistent"], 1)

    def test_open_model_requires_exact_schedule_and_generator_headroom(self):
        case = Case("open-read", 200, transport="encrypted-tunnel", arrival_rate=160)
        window = {"rps": 160, "all_unexpected": 0, "errors": 0, "window_started_ms": 1000,
                  "window_ended_ms": 121000, "interrupted_iterations": 0, "dropped_iterations": 0,
                  "p50_ms": 50, "p95_ms": 200, "p99_ms": 300, "started_iterations": 19200, "offered_iterations": 19200,
                  "completed_journeys": 19200, "requests": 19199}
        worker = {"warmup": {"exit": 0}, "measure": {"exit": 0, "generator_cpu_cores": .6},
                  "resources": {"k6_peak_rss_mib": 310, "generator_worker_peak_rss_mib": 120}}
        self.assertTrue(load_passed(case, window, window, worker))
        self.assertTrue(load_passed(case, window, {**window, "started_iterations": 19201, "completed_journeys": 19201}, worker))
        self.assertFalse(load_passed(case, window, {**window, "offered_iterations": 19199}, worker))
        self.assertFalse(load_passed(case, window, {**window, "dropped_iterations": 1}, worker))
        self.assertFalse(load_passed(case, window, {**window, "p99_ms": 2100}, worker))
        self.assertFalse(generator_headroom(case, {**worker, "measure": {"generator_cpu_cores": 1.9}}))
        self.assertFalse(generator_headroom(case, {**worker, "resources": {
            "k6_peak_rss_mib": 800, "generator_worker_peak_rss_mib": 120}}))

    def test_correlates_local_failure_without_attributing_missing_ingress(self):
        local = "20000000-0000-4000-8000-000000000001"
        missing = "20000000-0000-4000-8000-000000000002"
        def response(trace):
            return ("unexpected route=me method=GET status=502 outcome=http_error source=unattributed "
                    f"trace={trace} started_ms=1794062000000 ended_ms=1794062000050 protocol=HTTP/2.0 "
                    "server=other signature=bad_gateway echoed=false provider=-")
        failures = client_failures({"measure": {"log": response(local) + "\n" + response(missing)}})
        summary = join_traces(failures, f"2026/11/07 14:00:00 trace {local} ingress 0 accepted 1794062000010\n"
                             f"2026/11/07 14:00:00 trace {local} gateway 502 eof 1794062000020\ncount ingress 500")
        self.assertEqual(summary["gateway_events"], {"ingress": 500})
        self.assertEqual(summary["failures"][0]["local_events"][1]["reason"], "eof")
        self.assertEqual(summary["failures"][1]["local_events"], [])

    def test_budget_and_allowed_profiles(self):
        suite = first_suite(30)
        self.assertEqual(len(suite), 8)
        self.assertEqual(sum(case.profile == "mixed" for case in suite), 3)
        self.assertLess(budget(RATES, len(suite))["estimate_usd"], Decimal("1"))
        self.assertGreater(budget(RATES, 9)["estimate_usd"], Decimal("1"))
        for vus in (10, 50, 100, 200):
            self.assertEqual(Case("mixed", vus).report()["steady_seconds"], 120)
        self.assertIsNone(Case("login-burst", 200).report()["steady_seconds"])
        self.assertEqual(Case("mixed", 200, 30, 16, "encrypted-tunnel").report()["transport"], "encrypted-tunnel")
        with self.assertRaises(ValueError):
            Case("login-burst", 50, 30, 10, "encrypted-tunnel")
        with self.assertRaises(ValueError):
            Case("steady-read", 500)
        self.assertEqual(Case("mixed", 500, pool_size=32, transport="encrypted-tunnel").pool_size, 32)
        self.assertEqual(Case("mixed", 500, pool_size=32, transport="encrypted-tunnel", prefill_pool=True).pool_min_idle, 32)
        with self.assertRaises(ValueError):
            Case("mixed", 200, pool_size=32, transport="encrypted-tunnel")
        with self.assertRaises(ValueError):
            Case("mixed", 500, pool_size=16, transport="encrypted-tunnel", prefill_pool=True)
        self.assertEqual(Case("mixed", 200, pool_size=16, transport="encrypted-tunnel", diagnose_mixed=True,
                              prefill_pool=True, ramp_seconds=60, ramp_start_vus=1, commit_delay_us=200).pool_min_idle, 16)
        with self.assertRaises(ValueError):
            Case("mixed", 200, pool_size=16, transport="encrypted-tunnel", commit_delay_us=200)
        for vus in (500, 1000, 2500, 5000):
            case = Case("mixed", vus, transport="encrypted-tunnel")
            self.assertEqual(case.students, vus)
            self.assertGreater(case.section_capacity * 20, vus)
            self.assertLessEqual(case.request_limit, 1_125_000)
            self.assertLessEqual(case.response_mib, 2560)
            self.assertLess(budget(RATES, 1, case)["estimate_usd"], Decimal("1"))
        self.assertEqual(Case("open-read", 200, transport="encrypted-tunnel", arrival_rate=160).report()["executor"],
                         "constant-arrival-rate")
        with self.assertRaises(ValueError):
            Case("open-read", 200, arrival_rate=160)

    def test_window_classifications_and_missing_metrics(self):
        def metric(values):
            return {"values": values}
        actual = phase_metrics({"summary": {"state": {"testRunDurationMs": 121000}, "metrics": {
            "cloud_window_requests": metric({"count": 1200}),
            "cloud_outcomes{outcome:success}": metric({"count": 1198}),
            "cloud_outcomes{outcome:timeout}": metric({"count": 2}),
            "cloud_window_unexpected": metric({"count": 2}),
            "cloud_scenario_start_ms": metric({"value": 1000}),
            "cloud_window_latency": metric({"count": 1198, "p(50)": 10, "p(95)": 20, "p(99)": 30}),
            "cloud_iterations_started": metric({"count": 1000}), "iterations": metric({"count": 999}),
        }}}, 120)
        self.assertEqual(actual["rps"], 10)
        self.assertEqual(actual["window_ended_ms"], 121000)
        self.assertEqual(actual["outcomes"]["timeout"], 2)
        self.assertEqual(actual["outcomes"]["unavailable"], 0)
        self.assertEqual(actual["p95_ms"], 20)
        self.assertEqual(actual["interrupted_iterations"], 1)
        self.assertIsNone(actual["dropped_iterations"])
        self.assertIsNone(phase_metrics(None, 120)["rps"])
        empty = {"summary": {"metrics": {"cloud_window_latency": metric({"count": 0, "p(95)": 0})}}}
        self.assertIsNone(phase_metrics(empty, 120)["p95_ms"])
        self.assertFalse(load_passed(Case("steady-read", 10), actual, actual, {}))

    def test_prometheus_delta_pg_waits_and_missing_fields(self):
        text = """T\t1000
M\tprocess_cpu_usage 0.4
M\tjvm_memory_used_bytes{area="heap",id="a"} 1048576
M\tjvm_memory_used_bytes{area="heap",id="b"} 1048576
M\tenrollment_transaction_seconds_count 5
M\tenrollment_transaction_seconds_sum 0.3
P\t{"connections":6,"active":2,"waits":{"Lock:transactionid":1}}
T\t6000
M\tprocess_cpu_usage 0.8
M\tenrollment_transaction_seconds_count 10
M\tenrollment_transaction_seconds_sum 0.4
P\t{"connections":7,"active":3,"waits":{}}
T\t7000
M\tprocess_cpu_usage 1.0
P\t{"connections":99,"active":99,"waits":{"Lock:transactionid":7}}"""
        report = resource_summary(read_samples(text), {"window_started_ms": 1000, "window_ended_ms": 6000})
        self.assertEqual(report["spring_cpu_peak_percent"], 80)
        self.assertEqual(report["jvm_heap_peak_mib"], 2)
        self.assertEqual(report["postgres_connections_peak"], 7)
        self.assertEqual(report["postgres_lock_waits_peak"], 1)
        self.assertAlmostEqual(report["transaction_mean_ms"], 20)
        self.assertIsNone(report["section_lock_wait_mean_ms"])
        self.assertIsNone(resource_summary([], {})["postgres_lock_waits_peak"])

    def test_hot_sql_requires_exact_capacity_and_counts_denial_audits(self):
        observed = {"students": 205, "mixed_seats": 640, "active": 30, "withdrawn": 0, "hot_counter": 30,
                    "enrolled_outcomes": 30, "withdrawn_outcomes": 0, "full_outcomes": 20, "outcomes": 50,
                    "audit_events": 50, "authenticated_sessions": 50, "durable": True, "flyway_versions": "1,2,3,4,5,6,7,8"}
        import json
        args = (None, None, Case("hot-section", 50), {}, {"enrollments": 30, "section_full": 20})
        with patch("benchmark.query", side_effect=["0\n" * 8, json.dumps(observed)]):
            self.assertTrue(sql_checks(*args)["passed"])
        observed["audit_events"] -= 1
        with patch("benchmark.query", side_effect=["0\n" * 8, json.dumps(observed)]):
            self.assertFalse(sql_checks(*args)["passed"])


if __name__ == "__main__":
    unittest.main()
