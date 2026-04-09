import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tofufy.config import DEFAULT_CONFIG_FILES, discover_config_path, resolve_provider_args


class ConfigDiscoveryTests(unittest.TestCase):
    def test_default_provider_is_route53(self) -> None:
        args = resolve_provider_args([])
        self.assertEqual(args.provider, "aws-route53")
        self.assertIsNone(args.config)

    def test_explicit_config_wins(self) -> None:
        path = discover_config_path("aws-route53", "custom.json")
        self.assertEqual(path, Path("custom.json"))

    def test_route53_uses_new_default_before_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path.cwd()
            try:
                tmp_path = Path(tmp_dir)
                (tmp_path / DEFAULT_CONFIG_FILES["aws-route53"]).write_text("{}", encoding="utf-8")
                (tmp_path / "config-route53.json").write_text("{}", encoding="utf-8")
                os.chdir(tmp_path)
                path = discover_config_path("aws-route53")
            finally:
                os.chdir(cwd)

        self.assertEqual(path, Path(DEFAULT_CONFIG_FILES["aws-route53"]))

    def test_route53_falls_back_to_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path.cwd()
            try:
                tmp_path = Path(tmp_dir)
                (tmp_path / "config-route53.json").write_text("{}", encoding="utf-8")
                os.chdir(tmp_path)
                path = discover_config_path("aws-route53")
            finally:
                os.chdir(cwd)

        self.assertEqual(path, Path("config-route53.json"))

    def test_cloudflare_uses_provider_specific_default_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd = Path.cwd()
            try:
                tmp_path = Path(tmp_dir)
                os.chdir(tmp_path)
                path = discover_config_path("cloudflare")
            finally:
                os.chdir(cwd)

        self.assertEqual(path, Path(".tofufy-config-cloudflare.json"))


if __name__ == "__main__":
    unittest.main()
