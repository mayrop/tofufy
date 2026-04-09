import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tofufy.providers import route53


class Route53ProviderTests(unittest.TestCase):
    def test_finalize_args_sets_config_dir(self) -> None:
        args = argparse.Namespace()
        config_path = Path("/tmp/example/.tofufy-config-aws-route53.json")
        finalized = route53.finalize_args(args, {}, {}, config_path)
        self.assertEqual(finalized.config_dir, str(config_path.parent.resolve()))

    def test_route53_relative_output_paths_resolve_from_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            config_dir = base / "config"
            output_dir = config_dir / "generated"
            config_dir.mkdir()

            captured = {}

            def fake_build_session(profile):
                class FakeSession:
                    def client(self, name):
                        return object()

                return FakeSession()

            def fake_get_zone_details(client, zone_id, include_tags=True):
                return {"id": zone_id, "name": "example.com", "private_zone": False, "tags": {}, "vpcs": []}

            def fake_export_records(client, zone_id, zone_name, private_zone, output_dir_arg, **kwargs):
                captured["output_dir"] = output_dir_arg
                return (zone_name, "local.zone_records_example_com", output_dir_arg / "route53-records-example.com.tf", [])

            def fake_update_locals_file(locals_list, path):
                captured["locals_path"] = path

            def fake_write_imports_file(imports, path, **kwargs):
                captured["imports_path"] = path

            original_build_session = route53.build_session
            original_get_zone_details = route53.get_zone_details
            original_export_records = route53.export_records
            original_update_locals_file = route53.update_locals_file
            original_write_imports_file = route53.write_imports_file
            try:
                route53.build_session = fake_build_session
                route53.get_zone_details = fake_get_zone_details
                route53.export_records = fake_export_records
                route53.update_locals_file = fake_update_locals_file
                route53.write_imports_file = fake_write_imports_file

                args = argparse.Namespace(
                    zone_ids=["Z1"],
                    output_dir="generated",
                    locals_file="locals.tf",
                    imports_file="imports.tf",
                    zones_file="config-zones.tf",
                    single_zone=False,
                    single_zone_records_file="config-records.tf",
                    profile=None,
                    skip_hostnames=[],
                    only_hostnames=[],
                    export_target="records",
                    skip_zone_tags=False,
                    config_dir=str(config_dir),
                )
                result = route53.run(args)
            finally:
                route53.build_session = original_build_session
                route53.get_zone_details = original_get_zone_details
                route53.export_records = original_export_records
                route53.update_locals_file = original_update_locals_file
                route53.write_imports_file = original_write_imports_file

        self.assertEqual(result, 0)
        self.assertEqual(captured["output_dir"], output_dir.resolve())
        self.assertEqual(captured["locals_path"], (config_dir / "locals.tf").resolve())
        self.assertEqual(captured["imports_path"], (config_dir / "imports.tf").resolve())


if __name__ == "__main__":
    unittest.main()
