"""Load driver for the deployed Agent Engine orchestrator.

Uses `vertexai.agent_engines.get(...).stream_query(message=, user_id=)` —
the GA contract per the project memory note. Records every call to BigQuery
so the optimize loop, dashboards, and red-team gate all see real traffic.

Required env:
  GCP_PROJECT, GCP_LOCATION, ORCHESTRATOR_RESOURCE
Optional:
  TARGET_QPS         (default 1.0)
  DURATION_SECONDS   (default 120 — 2 minutes; bump for sustained load runs)
  CONCURRENCY        (default 4)
  RUN_ID             (default load-<timestamp>)

Defaults are intentionally conservative to keep accidental runs cheap.
For a sustained 30-minute soak: DURATION_SECONDS=1800 TARGET_QPS=2.0 CONCURRENCY=8
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from vertexai import agent_engines

from agent_eval.utils import trace_logger

PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
ORCH_RESOURCE = os.environ.get("ORCHESTRATOR_RESOURCE")
TARGET_QPS = float(os.environ.get("TARGET_QPS", "1.0"))
DURATION = int(os.environ.get("DURATION_SECONDS", "120"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))
RUN_ID = os.environ.get("RUN_ID") or f"load-{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"

if not PROJECT_ID or not ORCH_RESOURCE:
    print("❌ GCP_PROJECT and ORCHESTRATOR_RESOURCE must be set.")
    sys.exit(1)

PROMPTS = [
    ("billing", "I was double-charged on invoice INV-12345. Refund please."),
    ("billing", "Why did you charge me $149.99 last month when I downgraded to free?"),
    ("technical", "My app keeps crashing on startup with error 500. Help."),
    ("technical", "I'm getting HTTP 429 errors from the API. What's my rate limit?"),
    ("account", "I need to reset the email on my account. Old: a@b.com, new: c@d.com."),
    ("account", "My account says suspended — what do I do?"),
    ("billing", "Send me a copy of my last invoice."),
    ("technical", "Search the knowledge base for 'high latency'."),
    ("billing", "Refund INV-2231, duplicate $42 charge."),
    ("account", "Update my billing address to 123 Main St NYC 10001."),
]

OUT_DIR = Path("build/load_runs"); OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT_DIR / f"{RUN_ID}.jsonl"
SUMMARY_PATH = OUT_DIR / f"{RUN_ID}_summary.json"

_lock = threading.Lock()
_stats = {
    "started": 0, "succeeded": 0, "failed": 0,
    "latencies_ms": [],
    "by_category": {},
    "errors": [],
}


def _record(item: dict):
    with _lock:
        _stats["started"] += 1
        if item.get("error"):
            _stats["failed"] += 1
            _stats["errors"].append(item["error"])
        else:
            _stats["succeeded"] += 1
            _stats["latencies_ms"].append(item["latency_ms"])
        cat = item.get("category", "unknown")
        _stats["by_category"].setdefault(cat, {"n": 0, "errors": 0})
        _stats["by_category"][cat]["n"] += 1
        if item.get("error"):
            _stats["by_category"][cat]["errors"] += 1
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(item) + "\n")


def _engine_id(resource: str) -> str:
    return resource.split("/")[-1]


def _collect_text(events) -> str:
    parts = []
    for ev in events:
        content = ev.get("content") if isinstance(ev, dict) else None
        if not content:
            continue
        for p in (content.get("parts") or []):
            if isinstance(p, dict) and p.get("text"):
                parts.append(p["text"])
    return "".join(parts).strip()


def one_call(engine, idx: int):
    cat, prompt = random.choice(PROMPTS)
    user_id = f"{RUN_ID}-{idx % 50}"  # bucket users so memory bank gets reuse
    item = {
        "run_id": RUN_ID,
        "ts": dt.datetime.utcnow().isoformat() + "Z",
        "idx": idx,
        "category": cat,
        "prompt": prompt,
        "user_id": user_id,
    }
    t0 = time.perf_counter()
    try:
        events = list(engine.stream_query(message=prompt, user_id=user_id))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        text = _collect_text(events)
        item.update({
            "latency_ms": round(latency_ms, 1),
            "response": text[:600],
            "events_count": len(events),
        })
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        item.update({"latency_ms": round(latency_ms, 1), "error": str(exc)[:240]})
    _record(item)

    # Write per-call telemetry to BQ as well so the dashboard sees it.
    try:
        trace_logger.write_rows(PROJECT_ID, [{
            "timestamp": item["ts"],
            "run_id": RUN_ID,
            "source": "load_test",
            "experiment": "load-test",
            "reasoning_engine_id": _engine_id(ORCH_RESOURCE),
            "user_id": user_id,
            "session_id": None,
            "prompt": prompt,
            "response": item.get("response"),
            "category": cat,
        }])
    except Exception:
        pass


def main() -> None:
    print(f"=====================================================")
    print(f" 🚀 Load test")
    print(f" run_id      : {RUN_ID}")
    print(f" engine      : {ORCH_RESOURCE}")
    print(f" QPS target  : {TARGET_QPS}")
    print(f" duration    : {DURATION}s ({DURATION//60}m)")
    print(f" concurrency : {CONCURRENCY}")
    print(f" log         : {LOG_PATH}")
    print(f"=====================================================\n")

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    engine = agent_engines.get(ORCH_RESOURCE)

    end = time.time() + DURATION
    interval = 1.0 / TARGET_QPS
    idx = 0
    next_print = time.time() + 30

    # Inflight cap = CONCURRENCY. Without this, ThreadPoolExecutor queues
    # unboundedly: at 0.5 RPS submission and 40s avg latency, the queue
    # would balloon and shutdown would take hours after duration ends.
    inflight = threading.Semaphore(CONCURRENCY)

    def submit_one(i: int):
        try:
            one_call(engine, i)
        finally:
            inflight.release()

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        while time.time() < end:
            tick = time.time()
            inflight.acquire()  # blocks until a worker is free → caps inflight
            pool.submit(submit_one, idx)
            idx += 1
            if time.time() >= next_print:
                with _lock:
                    s = dict(_stats)
                lats = sorted(s["latencies_ms"])
                p50 = lats[len(lats)//2] if lats else 0
                p95 = lats[int(len(lats)*0.95)] if lats else 0
                remaining = int(end - time.time())
                print(f"[{idx:>5} req] ok={s['succeeded']} fail={s['failed']} "
                      f"p50={p50:.0f}ms p95={p95:.0f}ms remaining={remaining}s",
                      flush=True)
                next_print = time.time() + 30
            sleep_for = interval - (time.time() - tick)
            if sleep_for > 0:
                time.sleep(sleep_for)

        print("⏳ draining in-flight requests...", flush=True)
        # ThreadPoolExecutor.shutdown is implicit at context exit.

    with _lock:
        s = dict(_stats)
    lats = sorted(s["latencies_ms"])
    summary = {
        "run_id": RUN_ID,
        "engine": ORCH_RESOURCE,
        "started": s["started"],
        "succeeded": s["succeeded"],
        "failed": s["failed"],
        "duration_s": DURATION,
        "qps_actual": round(s["started"] / max(DURATION, 1), 2),
        "p50_ms": lats[len(lats)//2] if lats else None,
        "p95_ms": lats[int(len(lats)*0.95)] if lats else None,
        "p99_ms": lats[int(len(lats)*0.99)] if lats else None,
        "by_category": s["by_category"],
        "first_errors": s["errors"][:5],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    print(f"\n========== LOAD TEST SUMMARY ==========")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"=======================================")
    print(f"Per-call log : {LOG_PATH}")
    print(f"Summary      : {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
