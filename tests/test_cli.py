import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tofufy.cli import parse_args
from tofufy.providers.cloudflare_models import sanitize_dns_key


class CliProviderTests(unittest.TestCase):
    def test_parse_args_uses_cloudflare_provider_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / ".tofufy-config-cloudflare.json"
            config_path.write_text(
                json.dumps({"zone_id": "zone123", "account_id": "acct456"}),
                encoding="utf-8",
            )
            args = parse_args(["--provider", "cloudflare", "--config", str(config_path)])

        self.assertEqual(args.provider, "cloudflare")
        self.assertEqual(args.zone_id, "zone123")
        self.assertEqual(args.account_id, "acct456")

    def test_cloudflare_dns_key_model_keeps_type_suffix_for_non_a_cname(self) -> None:
        self.assertEqual(sanitize_dns_key("www.example.com", "TXT"), "www.example.com-txt")


if __name__ == "__main__":
    unittest.main()
