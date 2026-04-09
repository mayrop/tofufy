import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tofufy.providers.cloudflare import (
    build_logpush_job_map,
    normalize_account_lists,
    normalize_dns_records,
    normalize_managed_transforms,
    normalize_zone_rulesets,
    render_caching_file,
    render_logpush_file,
    render_logpush_imports,
    render_managed_transforms_imports,
    render_zone_setting_imports,
    render_managed_transforms_file,
    write_dns_file,
    write_generated_list_files,
    write_settings_files,
)
from tofufy.providers.cloudflare_models import group_zone_settings


class CloudflareProviderTests(unittest.TestCase):
    def test_normalize_dns_records_builds_stable_keys(self) -> None:
        records = normalize_dns_records(
            [
                {"id": "1", "name": "www.domain.com", "type": "CNAME", "content": "a.example.com"},
                {"id": "2", "name": "www.domain.com", "type": "TXT", "content": "v=spf1"},
            ],
            zone_id="zone1",
            zone_name="domain.com",
        )
        self.assertEqual([record.key for record in records], ["www", "www-txt"])

    def test_write_dns_file_renders_expected_map(self) -> None:
        records = normalize_dns_records(
            [{"id": "1", "name": "www.domain.com", "type": "CNAME", "content": "target.example.com"}],
            zone_id="zone1",
            zone_name="domain.com",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config-dns-records.tf"
            write_dns_file(records, path)
            content = path.read_text(encoding="utf-8")
        self.assertIn("dns_records = {", content)
        self.assertIn('"www" = {', content)
        self.assertIn('name = "www.domain.com"', content)

    def test_write_settings_files_rewrites_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            grouped = group_zone_settings({"cache_level": "simplified"}, ["caching"])
            write_settings_files(grouped, [], [], Path(tmp_dir), add_comments=True)
            content = (Path(tmp_dir) / "cloudflare-caching.tf").read_text(encoding="utf-8")
        self.assertIn('cache_level = "simplified"', content)
        self.assertIn("# Caching > Configuration", content)
        self.assertIn('resource "cloudflare_zone_setting" "caching"', content)

    def test_write_generated_list_files_creates_one_file_per_list(self) -> None:
        class FakeClient:
            def list_account_list_items(self, account_id, list_id):
                return [{"ip": "1.2.3.4", "comment": "test"}]

        definitions = normalize_account_lists(
            [{"id": "list1", "name": "whitelist_test", "kind": "ip", "description": "Test list"}],
            FakeClient(),
            "account1",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            write_generated_list_files(definitions, Path(tmp_dir), add_comments=True)
            content = (Path(tmp_dir) / "config-list-whitelist-test.tf").read_text(encoding="utf-8")
        self.assertIn("generated_cloudflare_list_whitelist_test", content)
        self.assertIn('ip = "1.2.3.4"', content)
        self.assertIn("# Test list", content)

    def test_render_caching_file_includes_rules_inline(self) -> None:
        class FakeClient:
            def get_zone_ruleset(self, zone_id, ruleset_id):
                return {
                    "id": ruleset_id,
                    "phase": "http_request_cache_settings",
                    "kind": "zone",
                    "name": "default",
                    "rules": [{"action": "block", "expression": "true", "enabled": True, "description": "cache rule"}],
                }

        definitions = normalize_zone_rulesets(
            [{"id": "rs1", "phase": "http_request_cache_settings", "kind": "zone", "name": "default"}],
            FakeClient(),
            "zone1",
        )
        settings = group_zone_settings({"cache_level": "simplified"}, ["caching"])["caching"]
        content = render_caching_file(settings, definitions[0], add_comments=True)
        self.assertIn("# Caching > Configuration > Cache Rules", content)
        self.assertIn("# cache rule", content)
        self.assertIn('action = "block"', content)

    def test_render_managed_transforms_file_contains_commented_maps(self) -> None:
        content = render_managed_transforms_file(
            {
                "request": {"add_bot_protection_headers": True},
                "response": {"add_security_headers": False},
            },
            add_comments=True,
        )
        self.assertIn("generated_managed_request_headers", content)
        self.assertIn("# Adds HTTP request headers with bot-related values: bot score, verified bot, threat score, JA3 and JA4 fingerprints.", content)
        self.assertIn('"add_security_headers" = false', content)

    def test_render_logpush_file_contains_output_options(self) -> None:
        content = render_logpush_file(
            [
                {
                    "id": 1,
                    "dataset": "http_requests",
                    "enabled": True,
                    "destination_conf": "s3://bucket/path",
                    "name": "domain-s3-http-logpush",
                    "output_options": {"field_names": ["RayID"], "timestamp_format": "rfc3339"},
                }
            ],
            add_comments=True,
        )
        self.assertIn('resource "cloudflare_logpush_job" "this"', content)
        self.assertIn("domain_s3_http_logpush = {", content)
        self.assertIn("output_options = {", content)

    def test_render_logpush_imports_uses_generated_keys(self) -> None:
        imports = render_logpush_imports(
            [{"id": 42, "dataset": "http_requests", "name": "domain-s3-http-logpush"}],
            "zone123",
        )
        content = "\n".join(imports)
        self.assertIn('to = cloudflare_logpush_job.this["domain_s3_http_logpush"]', content)
        self.assertIn('id = "zone123/42"', content)

    def test_render_zone_setting_imports_cover_each_generated_setting(self) -> None:
        grouped = group_zone_settings({"cache_level": "simplified", "browser_check": "on"}, ["caching", "security"])
        imports = render_zone_setting_imports(grouped, "zone123")
        content = "\n".join(imports)
        self.assertIn('to = cloudflare_zone_setting.caching["cache_level"]', content)
        self.assertIn('id = "zone123/cache_level"', content)
        self.assertIn('to = cloudflare_zone_setting.security["browser_check"]', content)

    def test_render_managed_transforms_imports_cover_headers_resource(self) -> None:
        imports = render_managed_transforms_imports(
            {"request": {"add_bot_protection_headers": True}, "response": {}},
            "zone123",
        )
        content = "\n".join(imports)
        self.assertIn("to = cloudflare_managed_transforms.headers", content)
        self.assertIn('id = "zone123"', content)

    def test_build_logpush_job_map_disambiguates_duplicate_datasets(self) -> None:
        mapped = build_logpush_job_map(
            [
                {"id": 1, "dataset": "http_requests"},
                {"id": 2, "dataset": "http_requests"},
            ]
        )
        self.assertEqual(list(mapped), ["http_requests", "http_requests_2"])

    def test_normalize_managed_transforms_maps_request_and_response(self) -> None:
        payload = {
            "managed_request_headers": [{"id": "add_bot_protection_headers", "enabled": True}],
            "managed_response_headers": [{"id": "add_security_headers", "enabled": False}],
        }
        normalized = normalize_managed_transforms(payload)
        self.assertEqual(normalized["request"]["add_bot_protection_headers"], True)
        self.assertEqual(normalized["response"]["add_security_headers"], False)
