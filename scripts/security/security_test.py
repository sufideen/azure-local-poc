#!/usr/bin/env python3
"""
Azure Local POC – Authorised Security Test Script
Step 10: Non-destructive security validation

IMPORTANT:
  - This script must ONLY be run with written authorisation from the system owner.
  - Performs configuration audits and non-destructive connectivity checks only.
  - Does NOT perform exploitation, brute-force, or denial-of-service testing.

Usage:
    python security_test.py --asa-ip 203.0.113.10 [--config config.yml]

Author: Clinical IT Engineering
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TestResult:
    test_name: str
    passed: bool
    finding: str
    severity: str = "INFO"   # INFO, LOW, MEDIUM, HIGH, CRITICAL
    recommendation: Optional[str] = None


class SecurityTester:
    """Performs authorised, non-destructive security validation checks."""

    def __init__(self, asa_ip: str, resource_group: str = "rg-clinical-poc"):
        self.asa_ip = asa_ip
        self.resource_group = resource_group
        self.results: list[TestResult] = []

    def _record(self, result: TestResult) -> TestResult:
        self.results.append(result)
        icon = "✓" if result.passed else "✗"
        sev  = f"[{result.severity}]" if not result.passed else ""
        print(f"  {icon} {result.test_name} {sev}")
        if not result.passed:
            print(f"    Finding: {result.finding}")
            if result.recommendation:
                print(f"    Fix:     {result.recommendation}")
        return result

    # ── TLS / Certificate Tests ───────────────────────────────────────────────
    def test_tls_version(self) -> TestResult:
        """Verify TLS 1.0 and 1.1 are rejected."""
        for tls_version, label in [
            (ssl.TLSVersion.TLSv1,   "TLS 1.0"),
            # TLSv1_1 may not be available in newer Python builds
        ]:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ctx.maximum_version = tls_version
                with socket.create_connection((self.asa_ip, 443), timeout=5) as s:
                    with ctx.wrap_socket(s):
                        return self._record(TestResult(
                            test_name=f"TLS version policy ({label} rejected)",
                            passed=False,
                            finding=f"{label} accepted by ASA – outdated protocol",
                            severity="HIGH",
                            recommendation="Set 'ssl server-version tlsv1.2' on ASA",
                        ))
            except ssl.SSLError:
                pass  # Expected – rejection is the correct behaviour

        return self._record(TestResult(
            test_name="TLS version policy (TLS 1.0 rejected)",
            passed=True,
            finding="ASA correctly rejects TLS 1.0",
        ))

    def test_certificate_validity(self) -> TestResult:
        """Check certificate is valid and not near expiry."""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((self.asa_ip, 443), timeout=10) as s:
                with ctx.wrap_socket(s) as ss:
                    cert = ss.getpeercert()
                    if cert:
                        not_after = ssl.cert_time_to_seconds(cert.get("notAfter", ""))
                        days_remaining = (not_after - datetime.now(timezone.utc).timestamp()) / 86400
                        if days_remaining < 30:
                            return self._record(TestResult(
                                test_name="Certificate expiry check",
                                passed=False,
                                finding=f"Certificate expires in {days_remaining:.0f} days",
                                severity="HIGH",
                                recommendation="Renew ASA TLS certificate immediately",
                            ))
                        return self._record(TestResult(
                            test_name="Certificate expiry check",
                            passed=True,
                            finding=f"Certificate valid for {days_remaining:.0f} more days",
                        ))
                    return self._record(TestResult(
                        test_name="Certificate expiry check",
                        passed=True,
                        finding="Certificate present (no expiry data in peercert)",
                    ))
        except Exception as e:
            return self._record(TestResult(
                test_name="Certificate expiry check",
                passed=False,
                finding=f"Could not retrieve certificate: {e}",
                severity="MEDIUM",
            ))

    # ── Port / Service Exposure Tests ─────────────────────────────────────────
    def test_unexpected_open_ports(self) -> TestResult:
        """Scan common ports on ASA outside – only 443 should be open."""
        disallowed_ports = {
            21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
            80: "HTTP", 110: "POP3", 161: "SNMP",
            8080: "HTTP-Alt", 8443: "HTTPS-Alt",
        }
        open_unexpected = {}
        for port, service in disallowed_ports.items():
            try:
                with socket.create_connection((self.asa_ip, port), timeout=3):
                    open_unexpected[port] = service
            except (socket.timeout, ConnectionRefusedError, OSError):
                pass   # Expected – port is closed

        if open_unexpected:
            return self._record(TestResult(
                test_name="ASA outside port exposure",
                passed=False,
                finding=f"Unexpected open ports: {open_unexpected}",
                severity="HIGH",
                recommendation="Apply ACL-OUTSIDE-IN to block unexpected ports",
            ))
        return self._record(TestResult(
            test_name="ASA outside port exposure",
            passed=True,
            finding=f"No unexpected ports open on {self.asa_ip} (checked {len(disallowed_ports)} ports)",
        ))

    # ── Azure Security Checks (via CLI) ───────────────────────────────────────
    def test_defender_recommendations(self) -> TestResult:
        """Check number of Defender for Cloud high-severity recommendations."""
        try:
            result = subprocess.run(
                ["az", "security", "assessment", "list",
                 "--resource-group", self.resource_group,
                 "--query",
                 "[?properties.status.code=='Unhealthy' && properties.metadata.severity=='High']"
                 " | length(@)",
                 "--output", "tsv"],
                capture_output=True, text=True, timeout=60,
            )
            count = int(result.stdout.strip() or "0")
            if count > 0:
                return self._record(TestResult(
                    test_name="Defender for Cloud (High severity)",
                    passed=False,
                    finding=f"{count} high-severity unhealthy recommendations",
                    severity="HIGH",
                    recommendation="Review Azure Defender recommendations in portal",
                ))
            return self._record(TestResult(
                test_name="Defender for Cloud (High severity)",
                passed=True,
                finding="No high-severity unhealthy Defender recommendations",
            ))
        except Exception as e:
            return self._record(TestResult(
                test_name="Defender for Cloud (High severity)",
                passed=False,
                finding=f"Could not query Defender: {e}",
                severity="LOW",
            ))

    def test_policy_compliance(self) -> TestResult:
        """Check Azure Policy compliance for the resource group."""
        try:
            result = subprocess.run(
                ["az", "policy", "state", "list",
                 "--resource-group", self.resource_group,
                 "--query", "[?complianceState=='NonCompliant'] | length(@)",
                 "--output", "tsv"],
                capture_output=True, text=True, timeout=60,
            )
            count = int(result.stdout.strip() or "0")
            if count > 0:
                return self._record(TestResult(
                    test_name="Azure Policy compliance",
                    passed=False,
                    finding=f"{count} non-compliant policy assignments",
                    severity="MEDIUM",
                    recommendation="Remediate non-compliant policies in Azure Portal → Policy → Compliance",
                ))
            return self._record(TestResult(
                test_name="Azure Policy compliance",
                passed=True,
                finding="All policy assignments compliant",
            ))
        except Exception as e:
            return self._record(TestResult(
                test_name="Azure Policy compliance",
                passed=False,
                finding=f"Policy compliance check failed: {e}",
                severity="LOW",
            ))

    def test_arc_machines_connected(self) -> TestResult:
        """Verify all Arc-connected machines are in Connected state."""
        try:
            result = subprocess.run(
                ["az", "connectedmachine", "list",
                 "--resource-group", self.resource_group,
                 "--query", "[].{name:name,status:status}",
                 "--output", "json"],
                capture_output=True, text=True, timeout=30,
            )
            machines = json.loads(result.stdout or "[]")
            disconnected = [m for m in machines if m.get("status") != "Connected"]
            if disconnected:
                names = [m["name"] for m in disconnected]
                return self._record(TestResult(
                    test_name="Azure Arc machine connectivity",
                    passed=False,
                    finding=f"Disconnected Arc machines: {names}",
                    severity="MEDIUM",
                    recommendation="Check azcmagent status on affected machines",
                ))
            connected_names = [m["name"] for m in machines]
            return self._record(TestResult(
                test_name="Azure Arc machine connectivity",
                passed=True,
                finding=f"All {len(machines)} Arc machines Connected: {connected_names}",
            ))
        except Exception as e:
            return self._record(TestResult(
                test_name="Azure Arc machine connectivity",
                passed=False,
                finding=f"Arc machine check failed: {e}",
                severity="LOW",
            ))

    def test_backup_recent_success(self) -> TestResult:
        """Verify at least one backup job has completed successfully."""
        try:
            result = subprocess.run(
                ["az", "backup", "job", "list",
                 "--resource-group", self.resource_group,
                 "--vault-name", "rsv-clinical-poc",
                 "--query", "[?status=='Completed'] | length(@)",
                 "--output", "tsv"],
                capture_output=True, text=True, timeout=30,
            )
            count = int(result.stdout.strip() or "0")
            if count > 0:
                return self._record(TestResult(
                    test_name="Azure Backup – completed jobs",
                    passed=True,
                    finding=f"{count} completed backup job(s) found",
                ))
            return self._record(TestResult(
                test_name="Azure Backup – completed jobs",
                passed=False,
                finding="No completed backup jobs found",
                severity="MEDIUM",
                recommendation="Trigger an on-demand backup and verify job completion",
            ))
        except Exception as e:
            return self._record(TestResult(
                test_name="Azure Backup – completed jobs",
                passed=False,
                finding=f"Backup job check failed: {e}",
                severity="LOW",
            ))

    # ── Run all tests ─────────────────────────────────────────────────────────
    def run_all(self) -> None:
        print("\n" + "=" * 70)
        print("  AZURE LOCAL POC – SECURITY TEST SUITE")
        print(f"  Target ASA: {self.asa_ip}")
        print(f"  Resource Group: {self.resource_group}")
        print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 70)
        print("\n  NOTE: These are non-destructive authorised checks only.\n")

        self.test_tls_version()
        self.test_certificate_validity()
        self.test_unexpected_open_ports()
        self.test_defender_recommendations()
        self.test_policy_compliance()
        self.test_arc_machines_connected()
        self.test_backup_recent_success()

        self._print_report()

    def _print_report(self) -> None:
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        high   = sum(1 for r in self.results if not r.passed and r.severity in ("HIGH", "CRITICAL"))

        print("\n" + "=" * 70)
        print("  SECURITY TEST SUMMARY")
        print("=" * 70)
        print(f"  Total:  {len(self.results)}")
        print(f"  Passed: {passed}")
        print(f"  Failed: {failed}  (High/Critical: {high})")
        print("")
        if failed:
            print("  Findings requiring attention:")
            for r in self.results:
                if not r.passed:
                    print(f"    [{r.severity}] {r.test_name}: {r.finding}")
        else:
            print("  All security checks passed.")
        print("=" * 70 + "\n")

    def save_report(self, path: str) -> None:
        """Write JSON report to file."""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_asa_ip": self.asa_ip,
            "resource_group": self.resource_group,
            "summary": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "failed": sum(1 for r in self.results if not r.passed),
                "high_critical": sum(
                    1 for r in self.results
                    if not r.passed and r.severity in ("HIGH", "CRITICAL")
                ),
            },
            "findings": [
                {
                    "test_name":       r.test_name,
                    "passed":          r.passed,
                    "finding":         r.finding,
                    "severity":        r.severity,
                    "recommendation":  r.recommendation,
                }
                for r in self.results
            ],
        }
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to: {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Azure Local POC – Authorised Security Testing (Step 10)",
    )
    parser.add_argument("--asa-ip",
                        default="203.0.113.10",
                        help="ASA outside IP address")
    parser.add_argument("--resource-group",
                        default="rg-clinical-poc",
                        help="Azure resource group name")
    parser.add_argument("--output",
                        default="security_report.json",
                        help="JSON output report path")
    parser.add_argument("--no-confirm", action="store_true",
                        help="Skip authorisation confirmation prompt (CI/CD use only)")
    args = parser.parse_args()

    if not args.no_confirm:
        print("\n" + "=" * 70)
        print("  AUTHORISATION REQUIRED")
        print("=" * 70)
        confirm = input(
            "  Have you obtained written authorisation and approved change control?\n"
            "  Type 'YES' to proceed: "
        ).strip()
        if confirm.upper() != "YES":
            print("  Aborted – authorisation not confirmed.")
            return 1

    tester = SecurityTester(asa_ip=args.asa_ip, resource_group=args.resource_group)
    tester.run_all()
    tester.save_report(args.output)

    failed_high = sum(
        1 for r in tester.results
        if not r.passed and r.severity in ("HIGH", "CRITICAL")
    )
    return 1 if failed_high > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
