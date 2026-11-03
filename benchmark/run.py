"""Measure what capturing costs (v10).

    benchmark/run.sh             light, medium and heavy load, both modes
    benchmark/run.sh --quick     a smoke run that takes seconds

Each load level runs the same Locust workload twice:

    direct     shopper ─────────────────────────> demo shop
    captured   shopper ──> capture proxy ──> demo shop ──(its SQL)──> API

The difference between the two runs is the cost of capture. The harness
starts its own stack on free ports with throwaway databases, so it never
touches a running demo, and writes what it measured — and how — to one
folder under benchmark/results/.
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "benchmark" / "drive.py"
RESULTS = ROOT / "benchmark" / "results"
LATEST = RESULTS / "latest"

CHECKOUT = "POST /api/v1/checkout"
BROWSE = "GET /api/v1/inventory"
AGGREGATED = "Aggregated"

_HOST = "127.0.0.1"
_STOCK = 10_000_000
_SAMPLE_S = 0.5
_BOOT_TIMEOUT_S = 60
_HTTP_TIMEOUT_S = 600
_MB = 1024 * 1024
_DROPPED_METRIC = "infrareplay_events_dropped_total"

# Versions recorded with every report, so a number can be traced to a build.
_PACKAGES = ("locust", "uvicorn", "fastapi", "starlette", "sqlalchemy", "aiosqlite", "httpx")


class Mode(str, Enum):
    DIRECT = "direct"
    CAPTURED = "captured"


@dataclass(frozen=True)
class Level:
    name: str
    users: int
    seconds: int


FULL = (Level("light", 5, 30), Level("medium", 20, 30), Level("heavy", 50, 30))
QUICK = (Level("smoke", 2, 5),)

FULL_WARMUP_S = 5
QUICK_WARMUP_S = 2


# ----------------------------------------------------------------------- stack


def free_port() -> int:
    with socket.socket() as s:
        s.bind((_HOST, 0))
        return s.getsockname()[1]


class Stack:
    """The API and the demo shop as real processes, with throwaway state."""

    def __init__(self, workdir: Path, api_db: str | None, shop_db: str | None) -> None:
        self._dir = workdir
        self._api_db = api_db or f"sqlite+aiosqlite:///{workdir}/meta.db"
        self._shop_db = shop_db or f"sqlite+aiosqlite:///{workdir}/shop.db"
        self._procs: dict[str, subprocess.Popen] = {}

        self.api_url = f"http://{_HOST}:{free_port()}"
        self.shop_url = f"http://{_HOST}:{free_port()}"
        self.payments_url = f"http://{_HOST}:{free_port()}"

    @property
    def blob_root(self) -> Path:
        return self._dir / "blobs"

    @property
    def backend(self) -> dict:
        return {"api_db": _scheme(self._api_db), "shop_db": _scheme(self._shop_db)}

    def pids(self) -> dict[str, int]:
        return {name: proc.pid for name, proc in self._procs.items()}

    def start(self) -> None:
        self._spawn(
            "api",
            "infrareplay.api.app:app",
            self.api_url,
            {
                "INFRAREPLAY_DATABASE_URL": self._api_db,
                "INFRAREPLAY_BLOB_ROOT": str(self.blob_root),
                "INFRAREPLAY_API_KEYS": "",
            },
        )
        self._spawn("payments", "examples.payment_gateway.app:app", self.payments_url, {})
        self._spawn(
            "shop",
            "examples.demo_shop.app:app",
            self.shop_url,
            {
                "DEMO_SHOP_DATABASE_URL": self._shop_db,
                # The race window is a deliberate sleep that demonstrates the
                # bug; it is not part of what a shop costs to run.
                "DEMO_SHOP_RACE_WINDOW_S": "0",
                "DEMO_SHOP_LOCK_INVENTORY": "1",
                "DEMO_SHOP_PAYMENTS_URL": self.payments_url,
                "INFRAREPLAY_API_URL": self.api_url,
                "INFRAREPLAY_API_KEY": "",
            },
        )

    def stop(self) -> None:
        for proc in self._procs.values():
            proc.terminate()

        for proc in self._procs.values():
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _spawn(self, name: str, module: str, url: str, env: dict) -> None:
        port = url.rsplit(":", 1)[1]
        log = open(self._dir / f"{name}.log", "w")

        self._procs[name] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", module, "--host", _HOST, "--port", port,
             "--no-access-log", "--log-level", "warning"],
            cwd=ROOT,
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        self._await_health(name, url)

    def _await_health(self, name: str, url: str) -> None:
        deadline = time.monotonic() + _BOOT_TIMEOUT_S

        while time.monotonic() < deadline:
            if self._procs[name].poll() is not None:
                raise RuntimeError(f"{name} exited at boot; see {self._dir / name}.log")

            try:
                if httpx.get(f"{url}/health", timeout=2).is_success:
                    return
            except httpx.HTTPError:
                pass

            time.sleep(0.25)

        raise RuntimeError(f"{name} did not answer /health within {_BOOT_TIMEOUT_S}s")


def _scheme(url: str) -> str:
    return url.split(":", 1)[0].split("+", 1)[0]


# --------------------------------------------------------------------- sampling


class Sampler(threading.Thread):
    """CPU and memory of each process under test, every half second."""

    def __init__(self, pids: dict[str, int]) -> None:
        super().__init__(daemon=True)
        self._procs = {name: psutil.Process(pid) for name, pid in pids.items()}
        self._cpu: dict[str, list[float]] = {name: [] for name in pids}
        self._rss: dict[str, int] = {name: 0 for name in pids}
        self._halt = threading.Event()

        for proc in self._procs.values():
            proc.cpu_percent(None)  # the first reading is always 0; prime it

    def run(self) -> None:
        while not self._halt.wait(_SAMPLE_S):
            for name, proc in self._procs.items():
                self._cpu[name].append(proc.cpu_percent(None))
                self._rss[name] = max(self._rss[name], proc.memory_info().rss)

    def finish(self) -> dict:
        self._halt.set()
        self.join()

        return {
            name: {
                "cpu_mean_pct": round(sum(c) / len(c), 1) if c else 0.0,
                "cpu_peak_pct": round(max(c), 1) if c else 0.0,
                "rss_peak_mb": round(self._rss[name] / _MB, 1),
            }
            for name, c in self._cpu.items()
        }


# ----------------------------------------------------------------------- locust


def run_locust(host: str, users: int, seconds: int, prefix: Path) -> dict:
    """The workload, through benchmark/drive.py, which times its own window."""

    done = subprocess.run(
        [sys.executable, str(DRIVER), "--host", host, "--users", str(users), "--seconds", str(seconds)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=seconds + 180,
    )

    if done.returncode != 0:
        raise RuntimeError(f"load driver failed: {done.stderr[-2000:]}")

    result = json.loads(done.stdout)
    Path(f"{prefix}.json").write_text(json.dumps(result, indent=2))

    return result


# ------------------------------------------------------------------------ runs


class Bench:
    def __init__(self, stack: Stack, outdir: Path) -> None:
        self._stack = stack
        self._out = outdir
        self._api = httpx.Client(base_url=stack.api_url, timeout=_HTTP_TIMEOUT_S)
        self._shop = httpx.Client(base_url=stack.shop_url, timeout=_HTTP_TIMEOUT_S)
        self.per_checkout = 0
        self.per_browse = 0

    def close(self) -> None:
        self._api.close()
        self._shop.close()

    def calibrate(self) -> None:
        """How many events one checkout and one browse produce when captured.

        Measured, not assumed, so completeness stays right when the shop's
        SQL changes.
        """

        recording = self._capture(lambda proxy: [
            httpx.post(f"{proxy}/api/v1/checkout", json=_ONE_CHECKOUT, headers=_HEADERS, timeout=30),
            httpx.get(f"{proxy}/api/v1/inventory", headers=_HEADERS, timeout=30),
        ])

        # Each request's events share its correlation id; its path names it.
        path_of = _inbound_paths(recording["events"])
        counts = Counter(path_of.get(e["correlation_id"]) for e in recording["events"])

        self.per_checkout = counts["/api/v1/checkout"]
        self.per_browse = counts["/api/v1/inventory"]

    def warm(self, seconds: int) -> None:
        self._reset()
        run_locust(self._stack.shop_url, 2, seconds, self._out / "warmup")

    def run(self, level: Level, mode: Mode) -> dict:
        self._reset()
        prefix = self._out / f"{level.name}-{mode.value}"

        if mode is Mode.DIRECT:
            sampler = Sampler(self._stack.pids())
            sampler.start()
            result = run_locust(self._stack.shop_url, level.users, level.seconds, prefix)
            return {"stats": result["rows"], "elapsed_s": result["elapsed_s"], "processes": sampler.finish()}

        return self._run_captured(level, prefix)

    def _run_captured(self, level: Level, prefix: Path) -> dict:
        port = free_port()
        started = self._api.post(
            "/api/captures",
            json={
                "plugin": "http_capture",
                "title": f"benchmark {level.name}, {level.users} users",
                "config": {"listen_port": port, "listen_host": _HOST, "upstream": self._stack.shop_url},
            },
        ).json()

        dropped_before = self._dropped()
        sampler = Sampler(self._stack.pids())
        sampler.start()
        result = run_locust(f"http://{_HOST}:{port}", level.users, level.seconds, prefix)
        stats = result["rows"]
        processes = sampler.finish()

        began = time.monotonic()
        reply = self._api.post(f"/api/captures/{started['recording_id']}/stop")
        finalise_s = time.monotonic() - began

        if reply.is_error:
            # A capture that fails under load is a result, not a crash.
            return {
                "stats": stats,
                "elapsed_s": result["elapsed_s"],
                "processes": processes,
                "capture": _failed_capture(f"stop returned {reply.status_code}: {reply.text[:300]}", finalise_s),
            }

        stopped = reply.json()

        return {
            "stats": stats,
            "elapsed_s": result["elapsed_s"],
            "processes": processes,
            "capture": self._capture_figures(
                stopped, result["elapsed_s"], finalise_s, self._dropped() - dropped_before
            ),
        }

    def _capture_figures(self, stopped: dict, elapsed_s: float, finalise_s: float, dropped: float) -> dict:
        """Did every captured request keep all its events?

        Judged per request, not by totals: requests still in flight when the
        load stops are captured but never counted by Locust, so a total
        comparison reads over 100% at the window's edge.
        """

        events = stopped["events"]
        sizes = Counter(e["correlation_id"] for e in events)
        path_of = _inbound_paths(events)
        expected = {"/api/v1/checkout": self.per_checkout, "/api/v1/inventory": self.per_browse}
        complete = sum(1 for corr, n in sizes.items() if n == expected.get(path_of.get(corr)))

        blob = self._stack.blob_root / "recordings" / stopped["recording_id"] / "recording.json"
        stored = blob.stat().st_size if blob.exists() else 0

        return {
            "recording_id": stopped["recording_id"],
            "events_captured": len(events),
            "requests_captured": len(sizes),
            "requests_complete": complete,
            "completeness_pct": round(complete / len(sizes) * 100, 2) if sizes else None,
            "events_dropped": int(dropped),
            "events_per_s": round(len(events) / elapsed_s, 1),
            "stored_bytes": stored,
            "stored_bytes_per_s": round(stored / elapsed_s),
            "finalise_s": round(finalise_s, 2),
        }

    def _capture(self, drive) -> dict:
        port = free_port()
        started = self._api.post(
            "/api/captures",
            json={
                "plugin": "http_capture",
                "title": "benchmark calibration",
                "config": {"listen_port": port, "listen_host": _HOST, "upstream": self._stack.shop_url},
            },
        ).json()

        self._reset()
        drive(f"http://{_HOST}:{port}")

        return self._api.post(f"/api/captures/{started['recording_id']}/stop").json()

    def _reset(self) -> None:
        self._shop.post("/api/v1/reset", params={"stock": _STOCK}).raise_for_status()

    def _dropped(self) -> float:
        """The API's own count of events it failed to store."""

        for line in self._api.get("/metrics").text.splitlines():
            if line.startswith(_DROPPED_METRIC):
                return float(line.split()[-1])

        return 0.0


def _inbound_paths(events: list[dict]) -> dict[str, str]:
    """correlation id -> the path the client asked for (outbound calls excluded)."""

    return {
        e["correlation_id"]: e["payload"]["path"]
        for e in events
        if e["event_type"] == "http.request" and e["payload"].get("direction") != "outbound"
    }


def _failed_capture(error: str, finalise_s: float) -> dict:
    return {
        "error": error,
        "events_captured": 0,
        "requests_captured": 0,
        "requests_complete": 0,
        "events_dropped": None,
        "completeness_pct": 0.0,
        "events_per_s": 0.0,
        "stored_bytes": 0,
        "stored_bytes_per_s": 0,
        "finalise_s": round(finalise_s, 2),
    }


_HEADERS = {"x-demo-user": "dana"}
_ONE_CHECKOUT = {
    "cart_id": "cart_calibration",
    "items": [{"sku": "AEROPRESS-GO", "qty": 1}],
    "payment_method": {"type": "card", "token": "tok_visa_bench"},
}


# ---------------------------------------------------------------------- report


def machine() -> dict:
    cpu = platform.processor() or ""

    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass

    return {
        "cpu": cpu,
        "logical_cores": os.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "os": platform.platform(),
        "python": platform.python_version(),
        "packages": {p: _version(p) for p in _PACKAGES},
    }


def _version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not installed"


def overhead(direct: dict, captured: dict) -> dict:
    d = direct["stats"].get(CHECKOUT, {})
    c = captured["stats"].get(CHECKOUT, {})
    da = direct["stats"].get(AGGREGATED, {})
    ca = captured["stats"].get(AGGREGATED, {})

    return {
        "throughput_change_pct": _pct(da.get("rps"), ca.get("rps")),
        "checkout_p50_added_ms": _delta(d.get("p50_ms"), c.get("p50_ms")),
        "checkout_p95_added_ms": _delta(d.get("p95_ms"), c.get("p95_ms")),
        "checkout_p99_added_ms": _delta(d.get("p99_ms"), c.get("p99_ms")),
    }


def _pct(before: float | None, after: float | None) -> float | None:
    if not before or after is None:
        return None

    return round((after - before) / before * 100, 1)


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None

    return round(after - before, 1)


def render(summary: dict) -> str:
    """The human report. Every number in it comes from summary.json."""

    m = summary["machine"]
    lines = [
        "# InfraReplay capture-overhead benchmark",
        "",
        f"Run {summary['started_at']} on {m['cpu']} ({m['logical_cores']} logical cores, "
        f"{m['ram_gb']} GB RAM). Databases: API {summary['backend']['api_db']}, "
        f"shop {summary['backend']['shop_db']}.",
        "",
        "## Cost of capture, per load level",
        "",
        "| Load | Users | Throughput | Checkout p50 | p95 | p99 | Events captured | Complete requests | Dropped | Stored | Finalise |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in summary["levels"]:
        o, cap = row["overhead"], row["captured"]["capture"]
        lines.append(
            f"| {row['level']} | {row['users']} | {_signed(o['throughput_change_pct'], '%')} "
            f"| {_signed(o['checkout_p50_added_ms'], ' ms')} | {_signed(o['checkout_p95_added_ms'], ' ms')} "
            f"| {_signed(o['checkout_p99_added_ms'], ' ms')} | {cap['events_captured']:,} "
            f"| {_num(cap['completeness_pct'], '%')} | {_count(cap.get('events_dropped'))} "
            f"| {cap['stored_bytes_per_s'] / 1024:,.0f} KB/s "
            f"| {cap['finalise_s']} s |"
        )

    lines += [
        "",
        "Throughput is the change in requests per second when capture is on; the latency",
        "columns are milliseconds added to the checkout at each percentile. Complete requests",
        "is the share of captured requests that kept every event they should have; Dropped is",
        "the API's own count of events it failed to store.",
        "",
        "## Raw results",
        "",
        "| Load | Mode | Requests | Failures | Req/s | Checkout p50 | p95 | p99 | Shop CPU | API CPU | Shop RSS | API RSS |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in summary["levels"]:
        for mode in (Mode.DIRECT, Mode.CAPTURED):
            run = row[mode.value]
            agg, chk = run["stats"].get(AGGREGATED, {}), run["stats"].get(CHECKOUT, {})
            shop, api = run["processes"].get("shop", {}), run["processes"].get("api", {})
            lines.append(
                f"| {row['level']} | {mode.value} | {agg.get('requests', 0):,} | {agg.get('failures', 0):,} "
                f"| {agg.get('rps', 0)} | {_num(chk.get('p50_ms'), ' ms')} | {_num(chk.get('p95_ms'), ' ms')} "
                f"| {_num(chk.get('p99_ms'), ' ms')} | {shop.get('cpu_mean_pct', 0)}% | {api.get('cpu_mean_pct', 0)}% "
                f"| {shop.get('rss_peak_mb', 0)} MB | {api.get('rss_peak_mb', 0)} MB |"
            )

    lines += [
        "",
        "## Method",
        "",
        f"- Workload: `benchmark/locustfile.py` — three checkouts per inventory browse, "
        f"think time 50–200 ms, users all spawned in the first second.",
        "- Locust is driven as a library (`benchmark/drive.py`), and every rate is requests "
        "divided by the wall-clock window it measured. Its `--run-time` flag is not used: on a "
        "slow filesystem its timer fired seconds early.",
        f"- Each level runs {summary['levels'][0]['seconds']} s direct, then the same "
        f"{summary['levels'][0]['seconds']} s captured, after a {summary['warmup_s']} s warm-up. "
        "Stock is reset before every run so no checkout fails for lack of it.",
        "- Every checkout makes a real HTTP call to the payment-gateway service, in both modes. "
        "Captured runs go through the real capture proxy, and the shop's agent streams its SQL "
        "and that outbound call to the API before each response leaves — the production path.",
        f"- One checkout produces {summary['events_per_checkout']} events and one browse "
        f"{summary['events_per_browse']}, measured by a calibration capture before the runs.",
        "- The shop's demonstration race window is set to 0 and inventory locking is on: the "
        "benchmark measures the shop, not the deliberate bug.",
        "- CPU is the mean of half-second samples of each process (100% = one core); memory is "
        "peak resident set size.",
        f"- Software: Python {m['python']}, "
        + ", ".join(f"{p} {v}" for p, v in m["packages"].items())
        + f". OS: {m['os']}.",
        "",
        "## Limits",
        "",
        "- Load generator, API, shop and databases share one machine, so they compete for CPU; "
        "absolute numbers are lower than on separate hosts. The direct-versus-captured "
        "comparison is like for like.",
        "- Each service runs one uvicorn worker.",
        "- SQLite serialises writes; under Postgres the absolute throughput differs. Run with "
        "`--api-db` and `--shop-db` to measure another backend.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "benchmark/run.sh            # this report",
        "benchmark/run.sh --quick    # a smoke run",
        "```",
    ]

    return "\n".join(lines) + "\n"


def _count(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _signed(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:+,.1f}{unit}"


def _num(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:,.1f}{unit}"


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="one tiny level, for smoke tests")
    parser.add_argument("--out", type=Path, help="results folder (default benchmark/results/<UTC time>)")
    parser.add_argument("--api-db", help="API database URL (default: a throwaway SQLite file)")
    parser.add_argument("--shop-db", help="shop database URL (default: a throwaway SQLite file)")
    parser.add_argument("--no-latest", action="store_true", help="do not update benchmark/results/latest")
    args = parser.parse_args()

    levels = QUICK if args.quick else FULL
    warmup_s = QUICK_WARMUP_S if args.quick else FULL_WARMUP_S
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = args.out or RESULTS / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix="infrareplay-bench-"))
    stack = Stack(workdir, args.api_db, args.shop_db)

    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": machine(),
        "backend": stack.backend,
        "warmup_s": warmup_s,
        "levels": [],
    }

    print(f"InfraReplay benchmark → {outdir}", flush=True)

    try:
        stack.start()
        bench = Bench(stack, outdir)

        bench.calibrate()
        summary["events_per_checkout"] = bench.per_checkout
        summary["events_per_browse"] = bench.per_browse
        bench.warm(warmup_s)

        for level in levels:
            print(f"  {level.name}: {level.users} users × {level.seconds}s, direct then captured", flush=True)

            direct = bench.run(level, Mode.DIRECT)
            captured = bench.run(level, Mode.CAPTURED)

            summary["levels"].append(
                {
                    "level": level.name,
                    "users": level.users,
                    "seconds": level.seconds,
                    "direct": direct,
                    "captured": captured,
                    "overhead": overhead(direct, captured),
                }
            )

        bench.close()

    finally:
        stack.stop()
        shutil.copytree(workdir, outdir / "logs", ignore=shutil.ignore_patterns("*.db", "blobs"), dirs_exist_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (outdir / "report.md").write_text(render(summary))

    if not args.no_latest:
        LATEST.mkdir(parents=True, exist_ok=True)
        shutil.copy2(outdir / "summary.json", LATEST / "summary.json")
        shutil.copy2(outdir / "report.md", LATEST / "report.md")

    print(render(summary))

    return 0


if __name__ == "__main__":
    sys.exit(main())
