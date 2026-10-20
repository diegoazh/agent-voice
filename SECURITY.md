# Security

## Reporting a vulnerability

Please do not open a public issue. Report privately through GitHub's
"Report a vulnerability" (Security tab) or email the maintainer. Expect an
acknowledgement within a few days.

## Automated scanning

- Local: `pre-commit` runs gitleaks, semgrep (severity ERROR) and bandit (see
  `.pre-commit-config.yaml`). The local bandit hook has no severity filter configured.
- CI: `.github/workflows/security.yml` runs gitleaks (full history), osv-scanner,
  trivy, semgrep and bandit, uploading SARIF to the Security tab. Failure thresholds:
  gitleaks any finding; osv-scanner known vulnerabilities; trivy HIGH/CRITICAL;
  semgrep severity ERROR; bandit severity HIGH. These are automated gates, not an audit.

This list mirrors `.github/workflows/security.yml` (and `trivy.yaml` for trivy) for CI
and `.pre-commit-config.yaml` for the local hooks, which are the source of truth: if they
differ from this page, they win.

## Run locally

```sh
pre-commit run --all-files                                   # all local hooks
gitleaks detect --source . --config .gitleaks.toml           # secrets, full history
osv-scanner scan --lockfile uv.lock                          # dependency vulns
trivy fs --config trivy.yaml .                               # vuln + misconfig + secret
semgrep scan --config p/python --config p/security-audit --severity ERROR --error .
uvx --from "bandit[toml]" bandit -c pyproject.toml -r src    # Python SAST
```

trufflehog is intentionally not wired into CI or pre-commit: it overlaps gitleaks
for secrets. Optional manual deep scan: `trufflehog git file://. --only-verified`.
