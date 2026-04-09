# tofufy

Generate Terraform/OpenTofu-friendly configuration from remote DNS and edge resources you already manage.

`tofufy` currently supports:

- `aws-route53`
- `cloudflare`

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for isolated execution

Provider-specific credentials:

- Route53: AWS credentials with permission to read hosted zones, records, and optionally zone tags
- Cloudflare: `TF_VAR_cloudflare_api_token` with read access to the resources you want to export

## Install

Persistent install:

```bash
uv tool install tofufy --from git+https://github.com/mayrop/tofufy.git
```

One-off usage:

```bash
uvx --from git+https://github.com/mayrop/tofufy.git tofufy --help
```

Inside this repo during development:

```bash
cd tofufy
uv run tofufy --help
```

## Provider Selection

```bash
uv run tofufy --provider aws-route53
uv run tofufy --provider cloudflare
```

You can also pass `--config path/to/file.json` to override config discovery explicitly.

## Provider Docs

- [Route53](./README-route53.md)
- [Cloudflare](./README-cloudflare.md)
