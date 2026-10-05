# Security policy

SAD analyzes untrusted code without executing it. Report vulnerabilities through the repository's GitHub **Security → Report a vulnerability** facility once private reporting is enabled. If unavailable, open an issue requesting a private contact without exploit details or sensitive data. There is no staffed response SLA yet. Security fixes target the latest release.

The AST engine has byte, step, recursion and cache limits. XML imports use defusedxml. Source-map source names are virtual identifiers and never authorize local reads. Crawler redirects are scope-checked, TLS verification stays enabled, and ambient proxy credentials are disabled. Imported captures and local analysis do not trigger network access.

The crawler intentionally supports internal targets for authorized assessments. Its same-origin policy is not an SSRF defense for an internet-facing service: DNS rebinding and access to explicitly seeded private hosts are not blocked. Integrators accepting untrusted crawl seeds must enforce network egress policy outside SAD. Run hostile workloads in a resource-limited worker; native parsers and bounded interpretation are not a process isolation boundary.

Output preserves request headers and bodies, which can include credentials and personal information. Protect output as sensitive assessment data. Raw HTTP import expects decoded, dechunked bodies; it does not transparently decompress captures.
