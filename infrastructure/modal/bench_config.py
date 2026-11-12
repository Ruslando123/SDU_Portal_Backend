"""Bounded Phase 8C workload and cost policy, shared with the generator."""
from dataclasses import asdict, dataclass
from decimal import Decimal

LEVELS = (10, 50, 100, 200, 500, 1000, 2500, 5000)
SESSION_LEVELS = (1000, 1500, 2000, 2500)
REDIS_SIDECAR_CPU_LIMITS = (12, 16)
BASE_POOLS = (10, 16)
SCALED_POOLS = (24, 32)
PROFILES = ("steady-read", "mixed", "login-burst", "hot-section", "open-read")
OPEN_RATES = (80, 120, 160, 200, 240, 300, 360, 400, 480, 600, 800, 1000)
MIXED_OPEN_RATES = (1200, 1400, 1600, 1800, 2000)
WARMUP_SECONDS = (30, 45, 60)
STEADY_SECONDS = 120
BURST_LIMIT_SECONDS = 30
SB_CPU, SB_MIB, SB_SECONDS = 1.5, 2048, 540
DB_CPU, DB_MIB, DB_STARTUP = 1, 1536, 90
RESOURCE_DB_CPU, RESOURCE_DB_MIB = 4, 8192
RESOURCE_EDGE_CPU, RESOURCE_EDGE_MIB = 1, 1024
RESOURCE_PROFILES = {"a": (1, 4, 8192), "b": (2, 2, 4096), "c": (2, 4, 8192)}
K6_CPU, K6_MIB, K6_SECONDS, K6_STARTUP, K6_IDLE = 1, 1024, 350, 45, 5
PREP_SECONDS, DRAIN_SECONDS = 105, 20
RECOVERY_SECONDS = 14
RESERVE_USD = Decimal("0.025")
REGION_MULTIPLIER = Decimal("1.75")


@dataclass(frozen=True)
class Case:
    profile: str
    vus: int
    warmup_seconds: int = 30
    pool_size: int = 10
    transport: str = "connect-token"
    arrival_rate: int | None = None
    spread_start: bool = False
    ramp_seconds: int = 0
    prefill_pool: bool = False
    split_database: bool = False
    diagnose_mixed: bool = False
    ramp_start_vus: int = 50
    commit_delay_us: int = 0
    hot_mixed: bool = False
    database_path: str | None = None
    backend_instances: int | None = None
    scale_out: bool = False
    catalog_path: str | None = None
    resource_profile: str | None = None
    private_db_sidecar: bool = False
    http_experiment: str | None = None
    diagnostic_only: bool = False
    session_read_mode: str | None = None
    session_storage: str | None = None
    sidecar_cpu_cores: int | None = None
    jvm_threads: str | None = None
    joined_academic_reads: bool = False
    hardware: str | None = None
    tomcat_threads: int | None = None
    section_read_experiment: str | None = None
    jdbc_security_pool: int | None = None

    def __post_init__(self):
        if (self.profile not in PROFILES or type(self.vus) is not int
                or self.vus not in (SESSION_LEVELS if self.session_storage else LEVELS)):
            raise ValueError("Unsupported profile or VU tier")
        if type(self.warmup_seconds) is not int or self.warmup_seconds not in WARMUP_SECONDS:
            raise ValueError("Warm-up must be 30, 45 or 60 seconds")
        if self.pool_size not in BASE_POOLS + SCALED_POOLS or (
                self.pool_size in SCALED_POOLS and (self.vus <= 200 or self.transport != "encrypted-tunnel")):
            raise ValueError("Pools above 16 are limited to scaled TLS benchmark cases")
        if self.transport not in ("connect-token", "encrypted-tunnel") or (
                self.transport == "encrypted-tunnel" and not self.timed):
            raise ValueError("The encrypted tunnel requires a timed benchmark")
        if self.vus > 200 and (not self.timed or self.transport != "encrypted-tunnel"):
            raise ValueError("Above 200 VUs only the authenticated TLS tunnel and timed profiles are allowed")
        if self.profile == "open-read":
            if self.arrival_rate not in OPEN_RATES or self.transport != "encrypted-tunnel":
                raise ValueError("Open read requires the authenticated tunnel and an approved arrival rate")
        elif self.arrival_rate is not None and not (self.jvm_threads and self.profile == "mixed"
                and self.vus == 2000 and self.arrival_rate in MIXED_OPEN_RATES):
            raise ValueError("Mixed arrival rates require a bounded 2,000-actor JVM experiment")
        if self.spread_start and not self.timed:
            raise ValueError("Start spread applies to timed profiles")
        if self.ramp_seconds not in (0, 20, 30, 60) or (self.ramp_seconds and (not self.closed or (self.vus <= 200 and not self.reconcile))):
            raise ValueError("The ramp applies to scaled closed profiles")
        if self.prefill_pool and not ((self.timed and self.vus > 200 and self.pool_size in SCALED_POOLS)
                                     or (self.reconcile and self.pool_size == 16)):
            raise ValueError("Pool prefill requires an eligible scaled or diagnostic workload")
        if self.reconcile and (self.profile != "mixed" or self.vus not in ((200, 500, *SESSION_LEVELS, 5000)
                               if self.resource_profile else (200, 500, 1000))
                               or self.transport != "encrypted-tunnel"):
            raise ValueError("Mixed diagnostics require encrypted mixed at 200, 500 or 1000 VUs")
        if self.ramp_start_vus not in (1, 5, 50) or (self.ramp_start_vus != 50 and not (self.reconcile and self.ramp_seconds)):
            raise ValueError("A gradual starting level is limited to diagnostic ramps")
        if self.commit_delay_us not in (0, 200) or (self.commit_delay_us and not self.reconcile):
            raise ValueError("WAL group commit tuning is limited to the bounded mixed diagnostic")
        if self.hot_mixed and (not self.reconcile or self.vus != 200):
            raise ValueError("The single-section comparison requires the 200-VU mixed diagnostic")
        if self.database_path is not None and (self.database_path not in ("direct", "session", "transaction")
                or not self.diagnose_mixed or self.split_database or self.pool_size != 16 or not self.prefill_pool
                or self.commit_delay_us or self.hot_mixed or self.vus not in (200, 500)):
            raise ValueError("Database path comparison requires shared mixed diagnostics with prefilled pool 16 and commit_delay=0")
        if self.backend_instances is not None and (self.backend_instances not in (1, 2, 3) or self.database_path is None):
            raise ValueError("Choose a compatible database path and one to three HAProxy backends")
        if self.scale_out and self.backend_instances != 2:
            raise ValueError("The larger resource budget is a separate two-backend experiment")
        if self.catalog_path is not None and (self.catalog_path not in ("primary", "replica")
                or self.database_path != "direct" or self.backend_instances is not None):
            raise ValueError("Catalog routing is an isolated direct-primary experiment")
        if self.resource_profile is not None and (
                self.resource_profile not in RESOURCE_PROFILES or not self.split_database or self.database_path is not None
                or self.backend_instances is not None or (self.pool_size != 16 and not self.direct_pool_test) or not self.prefill_pool
                or self.commit_delay_us or self.vus < 200 or self.ramp_seconds != 60 or self.ramp_start_vus != 1
                or not self.spread_start):
            raise ValueError("Resource scaling requires split mixed diagnostics, aggregate pool 16, and the fixed gradual ramp")
        if self.private_db_sidecar and (self.resource_profile != "a" or self.vus not in (200, 500, *SESSION_LEVELS) or self.hot_mixed):
            raise ValueError("The private database comparison requires single-backend profile A and mixed 200, 500, 1000 or 2500 VUs")
        if self.http_experiment is not None and (not self.private_scaled or self.http_experiment not in ("haproxy", "direct")):
            raise ValueError("HTTP route diagnostics require private Sidecar mixed 1000 or 2500")
        if self.diagnostic_only and (not self.http_experiment or self.vus != 2500):
            raise ValueError("The diagnostic ladder requires a Sidecar HTTP route and 2500 VUs")
        if self.session_read_mode is not None and (self.http_experiment != "direct" or self.pool_size != 16
                or self.session_read_mode not in ("baseline", "autocommit")):
            raise ValueError("Session checkout diagnostics require the direct TLS Sidecar with pool 16")
        if self.session_storage is not None and (self.session_storage not in ("jdbc", "redis")
                or self.http_experiment != "direct" or not self.private_db_sidecar
                or (self.pool_size != 16 and not self.redis_sidecar_pool_test)
                or self.session_read_mode is not None or self.diagnostic_only or self.warmup_seconds != 30):
            raise ValueError("Session runs require the direct TLS Sidecar and a supported pool with a 30s hold")
        if self.sidecar_cpu_cores is not None and (
                self.sidecar_cpu_cores not in REDIS_SIDECAR_CPU_LIMITS or self.session_storage != "redis"):
            raise ValueError("The larger Sidecar CPU limits are restricted to the Redis mixed benchmark")
        if self.jvm_threads is not None and (self.jvm_threads not in ("platform", "virtual") or not self.redis_sidecar_pool_test):
            raise ValueError("The JVM comparison requires the Redis Sidecar with 16 CPU and pool 24")
        if self.joined_academic_reads and self.jvm_threads is None:
            raise ValueError("Joined academic reads require an explicit JVM experiment")
        if self.hardware is not None and (self.hardware not in ("larger", "ha-one", "ha-two")
                or self.jvm_threads != "platform" or self.joined_academic_reads or self.arrival_rate):
            raise ValueError("Hardware tests require the platform-thread control with the original SQL")
        if self.tomcat_threads is not None and (self.tomcat_threads not in (80, 120) or self.hardware != "ha-one"
                or not self.redis_sidecar_pool_test or (self.vus != 2500 and self.section_read_experiment is None
                                                       and self.jdbc_security_pool is None)):
            raise ValueError("Pool diagnosis requires the 2,500-VU single-backend HAProxy Redis Sidecar profile")
        if self.section_read_experiment is not None and (self.section_read_experiment not in ("baseline", "joined")
                or self.profile != "mixed" or self.tomcat_threads != 80 or self.pool_size != 24 or self.vus not in (2000, 2500)):
            raise ValueError("Section read comparison requires mixed Redis Sidecar HAProxy at Tomcat 80 and HikariCP 24")
        if self.jdbc_security_pool is not None and (self.jdbc_security_pool not in (0, 4, 6) or self.tomcat_threads != 80
                or self.pool_size != 24 or self.profile != "mixed" or self.section_read_experiment is not None
                or self.vus not in (2000, 2500)):
            raise ValueError("Security pool comparison requires mixed Redis Sidecar HAProxy with Tomcat 80 and 24 combined connections")

    @property
    def private_scaled(self):
        return self.private_db_sidecar and self.vus >= 1000

    @property
    def private_loopback(self):
        return self.private_db_sidecar and (self.vus == 2500 or self.http_experiment is not None)

    @property
    def direct_pool_test(self):
        return (self.redis_sidecar_pool_test or self.private_db_sidecar and self.http_experiment == "direct"
                and self.vus == 2500 and self.pool_size == 24)

    @property
    def redis_sidecar_pool_test(self):
        return (self.session_storage == "redis" and self.sidecar_cpu_cores == 16
                and (self.pool_size == 24 or (self.pool_size == 32 and self.tomcat_threads is not None))
                and self.vus in (2000, 2500))

    @property
    def ramp_elapsed(self):
        return self.ramp_seconds + (30 if self.diagnostic_only else 0)

    @property
    def reconcile(self):
        return self.split_database or self.diagnose_mixed

    @property
    def topology(self):
        if self.private_db_sidecar:
            return "private-db-sidecar"
        if self.resource_profile:
            return "resource-isolated-tls"
        return "split-websocket" if self.split_database else "shared-sandbox"

    @property
    def instances(self):
        return RESOURCE_PROFILES[self.resource_profile][0] if self.resource_profile else self.backend_instances

    @property
    def pool_min_idle(self):
        return self.pool_size if self.prefill_pool else 2

    @property
    def sandbox_cpu(self):
        if self.resource_profile:
            return RESOURCE_PROFILES[self.resource_profile][1]
        return 2.5 if self.scale_out else SB_CPU

    @property
    def sidecar_cpu_limit(self):
        if self.hardware == "larger":
            return 20
        return self.sidecar_cpu_cores or (self.sandbox_cpu + RESOURCE_DB_CPU
                                        + (RESOURCE_EDGE_CPU if self.private_loopback else 0))

    @property
    def sidecar_memory_limit(self):
        return (self.sandbox_memory + RESOURCE_DB_MIB + (RESOURCE_EDGE_MIB if self.private_loopback else 0)
                + (4096 if self.hardware == "larger" else 0))

    @property
    def app_processes(self):
        return 2 if self.hardware == "ha-two" else self.instances

    @property
    def sandbox_memory(self):
        if self.resource_profile:
            return RESOURCE_PROFILES[self.resource_profile][2]
        return 3072 if self.scale_out else SB_MIB

    @property
    def closed(self):
        return self.profile in ("steady-read", "mixed")

    @property
    def timed(self):
        return self.closed or self.profile == "open-read"

    @property
    def students(self):
        if self.resource_profile:
            return 5000
        return max(500 if self.reconcile else 205, self.vus)

    @property
    def section_capacity(self):
        if self.resource_profile:
            return 252
        return max(32, (self.vus + 19) // 20 + 2)

    @property
    def prep_seconds(self):
        return max(PREP_SECONDS, (self.vus + 4) // 5 + 45)

    @property
    def generator_cpu(self):
        cores = next(cores for maximum, cores in ((500, 1), (1000, 2), (2500, 4), (5000, 8)) if self.vus <= maximum)
        if self.http_experiment:
            return 4
        return max(2, cores) if self.resource_profile else cores

    @property
    def generator_seconds(self):
        return max(K6_SECONDS, self.prep_seconds + self.ramp_elapsed + self.warmup_seconds
                   + (0 if self.diagnostic_only else STEADY_SECONDS) + 55
                   + (2 * RECOVERY_SECONDS if self.reconcile else 0))

    @property
    def sandbox_seconds(self):
        return max(SB_SECONDS, self.generator_seconds + (190 if self.reconcile else 130))

    @property
    def database_seconds(self):
        return self.sandbox_seconds + DB_STARTUP + (180 if self.resource_profile else 0) if self.split_database else None

    @property
    def request_limit(self):
        return max(80_000, self.vus * (225 + self.ramp_seconds), (self.arrival_rate or 0) * 135 + self.vus * 55)

    @property
    def response_mib(self):
        # Scale the cumulative byte allowance from the qualified private 500-VU case.
        private_window_budget = {500: 512, 1000: 1024, 1500: 1536, 2000: 2048, 2500: 2560}.get(self.vus, 256) if self.private_db_sidecar else 256
        return max(private_window_budget, ((self.vus + 499) // 500) * 256,
                   ((self.request_limit + 79_999) // 80_000) * 128)

    def report(self):
        return {**asdict(self), "fixture": "cloud-bench-v1" if self.students <= 205 else "cloud-bench-scaled-v1",
                "ramp_elapsed": self.ramp_elapsed,
                "pool_min_idle": self.pool_min_idle,
                "topology": self.topology, "operation_ledger": self.reconcile,
                "executor": "constant-arrival-rate" if self.arrival_rate else "constant-vus" if self.closed else "per-vu-iterations",
                "fixture_students": self.students, "fixture_mixed_seats": 20 * self.section_capacity,
                "gateway_request_limit": self.request_limit, "gateway_response_limit_mib": self.response_mib,
                "session_preparation_limit_seconds": self.prep_seconds,
                "steady_seconds": STEADY_SECONDS if self.timed else None,
                "burst_limit_seconds": None if self.timed else BURST_LIMIT_SECONDS,
                "warmup_vus": 5 if self.profile == "login-burst" else min(50, self.vus) if self.profile == "open-read" else self.vus,
                "warmup_profile": "mixed" if self.profile == "mixed" else "steady-read",
                "iterations_per_vu": None if self.timed else 1,
                "minimum_iteration_seconds": 1.25 if self.closed and not self.arrival_rate else None}


def first_suite(warmup_seconds):
    return [Case(profile, 50, warmup_seconds) for profile in PROFILES if profile != "open-read"
            for _ in range(3 if profile in ("steady-read", "mixed") else 1)]


def budget(rates, count, case=None):
    def rate(key):
        value = Decimal(str(rates[key]))
        if not value.is_finite() or value <= 0:
            raise ValueError(f"Invalid Modal rate: {key}")
        return value

    def compute(cpu, memory, seconds, suffix):
        return REGION_MULTIPLIER * seconds / 3600 * (
            Decimal(str(cpu)) * rate("cpu_hour_cost" + suffix) +
            Decimal(memory) / 1024 * rate("mem_gib_hour_cost" + suffix))

    sandbox = compute(case.sandbox_cpu if case else SB_CPU, case.sandbox_memory if case else SB_MIB,
                      case.sandbox_seconds if case else SB_SECONDS, "_sandbox")
    if case and case.resource_profile:
        edge = compute(RESOURCE_EDGE_CPU, RESOURCE_EDGE_MIB, case.sandbox_seconds, "_sandbox")
        if case.private_db_sidecar:
            if case.private_loopback:
                sandbox = compute(case.sidecar_cpu_limit, case.sidecar_memory_limit,
                                  case.sandbox_seconds, "_sandbox")
            else:
                sandbox = compute(case.sidecar_cpu_limit, case.sandbox_memory + RESOURCE_DB_MIB,
                                  case.sandbox_seconds, "_sandbox") + edge
            database = Decimal(0)
        else:
            sandbox = sandbox * case.instances + edge
            database = compute(RESOURCE_DB_CPU, RESOURCE_DB_MIB, case.database_seconds, "_sandbox")
    else:
        database = compute(DB_CPU, DB_MIB, case.database_seconds, "_sandbox") if case and case.split_database else Decimal(0)
    cores = case.generator_cpu if case else K6_CPU
    duration = case.generator_seconds if case else K6_SECONDS
    function = compute(cores, K6_MIB * cores, K6_STARTUP + duration + K6_IDLE, "")
    egress = rate("egress_gib_cost") * ((case.response_mib + 255) // 1024 + 1 if case else 1)
    if case and case.split_database:
        egress += rate("egress_gib_cost") * 2
    if case and case.resource_profile:
        egress += rate("egress_gib_cost") * (1 + case.response_mib // 512)
    estimate = sandbox + database + function + egress
    return {"sandbox_compute_usd": sandbox, "database_compute_usd": database, "generator_compute_usd": function, "egress_allowance_usd": egress,
            "per_run_estimate_usd": estimate, "suite_reserve_usd": RESERVE_USD,
            "estimate_usd": estimate * count + RESERVE_USD}
