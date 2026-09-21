"""GCP IAM Authentication helper for Cloud Run service-to-service communication."""
import os
import urllib.request
import urllib.error
from typing import Optional

def get_gcp_id_token(audience: Optional[str] = None) -> Optional[str]:
    """Fetch GCP IAM ID token from metadata server for service-to-service calls.
    Returns None if running locally or if audience is not configured.
    """
    target_audience = (
        audience
        or os.environ.get("GAME_SERVER_AUDIENCE")
        or os.environ.get("GAME_SERVER_URL")
    )
    if not target_audience or target_audience.startswith("http://127.0.0.1") or target_audience.startswith("http://localhost"):
        return None
    try:
        url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={target_audience}"
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None
