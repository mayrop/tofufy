from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CloudflareCredentials:
    api_token: str


def load_credentials(api_token: Optional[str] = None) -> CloudflareCredentials:
    token = api_token or os.getenv("TF_VAR_cloudflare_api_token")
    if not token:
        raise ValueError("Missing Cloudflare API token. Set TF_VAR_cloudflare_api_token.")
    return CloudflareCredentials(api_token=token)


class CloudflareAPIClient:
    base_url = "https://api.cloudflare.com/client/v4"

    def __init__(self, credentials: CloudflareCredentials) -> None:
        self.credentials = credentials

    def list_dns_records(self, zone_id: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            payload = self._get_json(
                f"/zones/{zone_id}/dns_records",
                params={"page": page, "per_page": per_page},
            )
            result = payload.get("result") or []
            records.extend(result)
            result_info = payload.get("result_info") or {}
            total_pages = int(result_info.get("total_pages") or 1)
            if page >= total_pages:
                break
            page += 1
        return records

    def list_zone_settings(self, zone_id: str) -> Dict[str, Any]:
        payload = self._get_json(f"/zones/{zone_id}/settings")
        settings = payload.get("result") or []
        return {
            setting.get("id"): setting.get("value")
            for setting in settings
            if setting.get("id") is not None
        }

    def list_zone_rulesets(self, zone_id: str) -> List[Dict[str, Any]]:
        payload = self._get_json(f"/zones/{zone_id}/rulesets")
        return payload.get("result") or []

    def get_zone_ruleset(self, zone_id: str, ruleset_id: str) -> Dict[str, Any]:
        payload = self._get_json(f"/zones/{zone_id}/rulesets/{ruleset_id}")
        return payload.get("result") or {}

    def get_managed_transforms(self, zone_id: str) -> Dict[str, Any]:
        payload = self._get_json(f"/zones/{zone_id}/managed_headers")
        return payload.get("result") or {}

    def list_zone_logpush_jobs(self, zone_id: str) -> List[Dict[str, Any]]:
        payload = self._get_json(f"/zones/{zone_id}/logpush/jobs")
        return payload.get("result") or []

    def get_zone_logpush_job(self, zone_id: str, job_id: int | str) -> Dict[str, Any]:
        payload = self._get_json(f"/zones/{zone_id}/logpush/jobs/{job_id}")
        return payload.get("result") or {}

    def list_account_lists(self, account_id: str) -> List[Dict[str, Any]]:
        payload = self._get_json(f"/accounts/{account_id}/rules/lists")
        return payload.get("result") or []

    def list_account_list_items(self, account_id: str, list_id: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"per_page": 500}
            if cursor:
                params["cursor"] = cursor
            payload = self._get_json(f"/accounts/{account_id}/rules/lists/{list_id}/items", params=params)
            items.extend(payload.get("result") or [])
            result_info = payload.get("result_info") or {}
            cursors = result_info.get("cursors") or {}
            cursor = cursors.get("after")
            if not cursor:
                break
        return items

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        request_target = f"GET {path}{query}"
        request = urllib.request.Request(
            f"{self.base_url}{path}{query}",
            headers={
                "Authorization": f"Bearer {self.credentials.api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValueError(
                f"Cloudflare API request failed for {request_target}: {exc.code} {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ValueError(
                f"Cloudflare API request failed for {request_target}: {exc.reason}"
            ) from exc

        import json

        payload = json.loads(body)
        if not payload.get("success", False):
            errors = payload.get("errors") or []
            raise ValueError(
                f"Cloudflare API request was unsuccessful for {request_target}: {errors}"
            )
        return payload
