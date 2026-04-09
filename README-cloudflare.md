# Cloudflare

## Default config file

`.tofufy-config-cloudflare.json`

If no explicit `--config` is passed, `tofufy` looks for the provider default in the current working directory.

## Example config

```json
{
  "account_id": "youraccountid",
  "zone_id": "yourzoneid",
  "zone_name": "domain.com",
  "output_dir": "../tf-cloudflare-resources",
  "dns_file": "config-dns-records.tf",
  "imports_file": "imports.tf",
  "only_hostnames": [],
  "only_sections": [],
  "add_comments": true
}
```

## Common commands

```bash
cd tofufy
export TF_VAR_cloudflare_api_token=your_token
uv run tofufy --provider cloudflare --config path/to/.tofufy-config-cloudflare.json
```

## Key settings

- `account_id`: required for account-level resources such as lists
- `zone_id`: required zone identifier
- `zone_name`: recommended so DNS keys can be generated relative to the zone
- `output_dir`: destination directory for generated files
- `dns_file`: DNS locals file, usually `config-dns-records.tf`
- `imports_file`: all Cloudflare import blocks in a single file
- `only_hostnames`: include-only regex list for DNS export
- `only_sections`: limit settings export to specific sections
- `add_comments`: emit curated explanatory comments in generated Terraform files

`output_dir` is resolved relative to the config file location when it is not absolute.

## Outputs

Cloudflare rewrites the generated files on each run. The current supported outputs are:

- `config-dns-records.tf`
- `imports.tf`
- `cloudflare-caching.tf`
- `cloudflare-network.tf`
- `cloudflare-security.tf`
- `cloudflare-ssl-tls.tf`
- `cloudflare-speed.tf`
- `cloudflare-scrape-shield.tf`
- `cloudflare-rules.tf`
- `cloudflare-analytics.tf`
- `config-list-*.tf`

## Resource coverage

Cloudflare currently exports:

- DNS records
- zone settings for:
  - `caching`
  - `network`
  - `security`
  - `ssl_tls`
  - `speed`
  - `scrape_shield`
- account filter lists
- supported zone rulesets
- managed transforms
- logpush jobs

Notes:

- rulesets are only integrated for the supported user-managed phases `http_request_cache_settings`, `http_request_firewall_custom`, and `http_ratelimit`
- unsupported or system-managed phases are skipped
- when a supported Cloudflare setting is missing from the API payload, `tofufy` preserves the existing local value instead of dropping the key
- `tls_client_auth` is intentionally preserved from local config

## File naming

Generated list config files use:

- `config-list-*.tf`

Examples:

- `config-list-blacklist-ips.tf`
- `config-list-whitelist-office-network.tf`

## Typical workflow

1. Create `.tofufy-config-cloudflare.json`.
2. Export `TF_VAR_cloudflare_api_token`.
3. Run `tofufy --provider cloudflare`.
4. Run `tofu fmt`.
5. Inspect the diff.
6. Run `tofu plan`.
