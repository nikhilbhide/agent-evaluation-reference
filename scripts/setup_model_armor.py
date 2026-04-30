"""
Provision a Model Armor sanitization template and persist the resource path.

Uses the Model Armor REST API directly (the SDK is preview/alpha and we
don't want to add a fragile dep). ADC auth is sufficient.

Outputs the fully-qualified template name to `model_armor_template.txt`,
which the deploy scripts then propagate to each Agent Engine as the
`MODEL_ARMOR_TEMPLATE` env var. The agents' before_model callback reads
that env var to know which template to call sanitize against.

Required env:
  GCP_PROJECT
Optional:
  GCP_LOCATION       (default us-central1)
  MA_TEMPLATE_ID     (default techcorp-security-gate)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import google.auth
import google.auth.transport.requests
import requests

PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
TEMPLATE_ID = os.environ.get("MA_TEMPLATE_ID", "techcorp-security-gate")

OUT_FILE = Path(__file__).resolve().parents[1] / "model_armor_template.txt"


def _auth_headers() -> dict[str, str]:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }


def _api_root() -> str:
    return f"https://modelarmor.{LOCATION}.rep.googleapis.com/v1"


def _template_resource() -> str:
    return f"projects/{PROJECT_ID}/locations/{LOCATION}/templates/{TEMPLATE_ID}"


# Filter config: PI + jailbreak, malicious URI, and a confidence threshold
# strict enough to block the canonical adversarial cases in
# data/golden_dataset.json.
TEMPLATE_BODY: dict = {
    "filterConfig": {
        "raiSettings": {
            "raiFilters": [
                {"filterType": "DANGEROUS", "confidenceLevel": "MEDIUM_AND_ABOVE"},
                {"filterType": "HARASSMENT", "confidenceLevel": "MEDIUM_AND_ABOVE"},
                {"filterType": "SEXUALLY_EXPLICIT", "confidenceLevel": "MEDIUM_AND_ABOVE"},
                {"filterType": "HATE_SPEECH", "confidenceLevel": "MEDIUM_AND_ABOVE"},
            ],
        },
        "piAndJailbreakFilterSettings": {
            "filterEnforcement": "ENABLED",
            "confidenceLevel": "LOW_AND_ABOVE",
        },
        "maliciousUriFilterSettings": {"filterEnforcement": "ENABLED"},
        "sdpSettings": {"basicConfig": {"filterEnforcement": "ENABLED"}},
    },
}


def upsert_template() -> str:
    headers = _auth_headers()
    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}"

    # Check existence first.
    get_url = f"{_api_root()}/{_template_resource()}"
    r = requests.get(get_url, headers=headers, timeout=20)
    if r.status_code == 200:
        # Update in place.
        update_url = f"{get_url}?updateMask=filterConfig"
        r2 = requests.patch(update_url, headers=headers, data=json.dumps(TEMPLATE_BODY), timeout=20)
        r2.raise_for_status()
        print(f"   ✅ updated existing template {TEMPLATE_ID}")
    elif r.status_code == 404:
        create_url = f"{_api_root()}/{parent}/templates?templateId={TEMPLATE_ID}"
        r2 = requests.post(create_url, headers=headers, data=json.dumps(TEMPLATE_BODY), timeout=30)
        r2.raise_for_status()
        print(f"   ✅ created template {TEMPLATE_ID}")
    else:
        r.raise_for_status()

    return _template_resource()


def main() -> None:
    if not PROJECT_ID:
        print("❌ GCP_PROJECT must be set.")
        sys.exit(1)

    print(f"🛡️  Provisioning Model Armor template {TEMPLATE_ID} in {PROJECT_ID}/{LOCATION}")

    try:
        resource = upsert_template()
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"❌ Model Armor API call failed: {exc}\n{body}")
        print("   Hint: enable modelarmor.googleapis.com and ensure the caller has "
              "roles/modelarmor.admin on the project.")
        sys.exit(1)

    OUT_FILE.write_text(resource + "\n")
    print(f"\n✅ Template resource: {resource}")
    print(f"   wrote {OUT_FILE.relative_to(Path.cwd()) if OUT_FILE.is_relative_to(Path.cwd()) else OUT_FILE}")
    print("\nNext: re-deploy agents so MODEL_ARMOR_TEMPLATE env var is propagated.")
    print("      python scripts/redeploy_all.py")


if __name__ == "__main__":
    main()
