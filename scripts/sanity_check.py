"""
Agent Sanity Check — runs BEFORE the full Vertex AI evaluation.

PURPOSE:
  The full evaluation takes 30-90 seconds and costs money (Vertex AI API calls).
  The sanity check takes < 5 seconds and is FREE.
  It catches the obvious failures early so we don't waste evaluation budget.

WHAT IT CHECKS:
  1. /health endpoint returns 200 (process is alive)
  2. /ready  endpoint returns 200 (model loaded, pod is serving)
  3. /version endpoint returns the EXPECTED image version (we're hitting the
     right canary pod, not accidentally hitting stable)
  4. /predict responds to a trivial prompt within a latency threshold
  5. The response is non-empty (agent didn't return a blank string)
  6. The response does NOT contain error keywords like "Agent Error:"

EXIT CODES:
  0 — all checks passed, proceed to full evaluation
  1 — one or more checks failed, abort deployment

USAGE:
  # During CD pipeline:
  python scripts/sanity_check.py \\
      --endpoint http://localhost:8080 \\
      --expected-version abc1234 \\
      --latency-threshold-ms 5000

  # Quick local test against running Docker container:
  python scripts/sanity_check.py --endpoint http://localhost:8080
"""

import argparse
import sys
import time
import requests
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sanity_check")

# Trivial prompt — just checks the agent responds at all.
# NOT a quality test; full quality testing is done by agent-eval.
SANITY_PROMPT = "Hello, I need help with my account."
ERROR_KEYWORDS = ["Agent Error:", "500 Internal", "503 Service"]


def check(name: str, passed: bool, detail: str = ""):
    icon = "✅" if passed else "❌"
    msg = f"{icon} {name}"
    if detail:
        msg += f" — {detail}"
    logger.info(msg)
    return passed


from typing import Optional

def run_sanity_check(endpoint: str, expected_version: Optional[str], latency_threshold_ms: float) -> bool:
    """
    Runs all sanity checks against the given endpoint.
    Returns True if all checks pass, False otherwise.
    """
    results = []

    # ── Check 1: /health ──────────────────────────────────────────────────────
    try:
        r = requests.get(f"{endpoint}/health", timeout=5)
        results.append(check("Liveness (/health)", r.status_code == 200, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(check("Liveness (/health)", False, str(e)))
        logger.error("Cannot reach endpoint at all — aborting remaining checks.")
        return False

    # ── Check 2: /ready ───────────────────────────────────────────────────────
    try:
        r = requests.get(f"{endpoint}/ready", timeout=10)
        results.append(check("Readiness (/ready)", r.status_code == 200, f"HTTP {r.status_code}"))
    except Exception as e:
        results.append(check("Readiness (/ready)", False, str(e)))

    # ── Check 3: version matches expected canary tag ───────────────────────────
    # Note: Version mismatch is a warning, not a failure. During rolling updates, the pod
    # may still be on an old version when this check runs. The actual check is that
    # /predict is responding correctly (which happens below).
    if expected_version:
        try:
            r = requests.get(f"{endpoint}/version", timeout=5)
            actual_version = r.json().get("version", "")
            version_ok = actual_version == expected_version
            if version_ok:
                results.append(check("Version match (/version)", True, f"version={actual_version}"))
            else:
                results.append(check("Version match (/version)", False, f"expected={expected_version} got={actual_version}"))
                logger.error(
                    f"Version mismatch: expected={expected_version} got={actual_version}. "
                    f"Aborting as we may be targeting the wrong pod."
                )
        except Exception as e:
            results.append(check("Version match (/version)", False, str(e)))
    else:
        logger.info("⏭  Version check skipped (--expected-version not provided)")

    # ── Check 4 & 5 & 6: /predict latency, non-empty response, no error keywords
    try:
        t0 = time.perf_counter()
        r = requests.post(
            f"{endpoint}/predict",
            json={"prompt": SANITY_PROMPT},
            timeout=latency_threshold_ms / 1000 + 5,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        # Check 4: HTTP 200
        results.append(check("Predict status", r.status_code == 200, f"HTTP {r.status_code}"))

        if r.status_code == 200:
            data = r.json()
            response_text = data.get("response", "")

            # Check 5: latency within threshold
            results.append(check(
                "Predict latency",
                latency_ms <= latency_threshold_ms,
                f"{latency_ms:.0f}ms (threshold: {latency_threshold_ms:.0f}ms)"
            ))

            # Check 6: response non-empty
            results.append(check(
                "Response non-empty",
                len(response_text.strip()) > 0,
                f"length={len(response_text)}"
            ))

            # Check 7: no error keywords (catches "Agent Error: 404 ...")
            no_errors = not any(kw in response_text for kw in ERROR_KEYWORDS)
            results.append(check(
                "Response has no error markers",
                no_errors,
                response_text[:80] if not no_errors else "OK"
            ))

    except requests.exceptions.Timeout:
        results.append(check(
            "Predict latency",
            False,
            f"Timed out after {latency_threshold_ms + 5000:.0f}ms"
        ))
    except Exception as e:
        results.append(check("Predict call", False, str(e)))

    # ── Summary ───────────────────────────────────────────────────────────────
    passed = all(results)
    total = len(results)
    ok = sum(results)
    logger.info(f"{'─'*50}")
    logger.info(f"Sanity check result: {ok}/{total} checks passed")
    if passed:
        logger.info("✅ SANITY CHECK PASSED — proceeding to full evaluation")
    else:
        logger.error("❌ SANITY CHECK FAILED — aborting deployment. Do NOT run full evaluation.")
    return passed


def main():
    parser = argparse.ArgumentParser(description="Agent sanity check before full evaluation")
    parser.add_argument("--endpoint", required=True, help="Base URL of the agent (e.g. http://localhost:8080)")
    parser.add_argument("--expected-version", default=None, help="Expected APP_VERSION from /version (e.g. git SHA)")
    parser.add_argument("--latency-threshold-ms", type=float, default=5000,
                        help="Max acceptable first-token latency in ms (default: 5000)")
    args = parser.parse_args()

    passed = run_sanity_check(
        endpoint=args.endpoint.rstrip("/"),
        expected_version=args.expected_version,
        latency_threshold_ms=args.latency_threshold_ms,
    )
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
