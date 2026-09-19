# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
**Report a vulnerability** button on the Security tab (private reporting), or email
[sufyan@ict-cloud.solutions](mailto:sufyan@ict-cloud.solutions).

This is a personal portfolio and learning project, so there is no formal SLA, but
reports are read and acknowledged.

## Secrets and data

- No credentials are stored in this repository. Deployment uses OIDC or environment
  secrets configured outside the code, and `.env` files are git-ignored.
- Sample data and example values are fictional.
- A secret scan (gitleaks) runs on every pull request.
