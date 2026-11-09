"""Mailer-side event capture for Helm.

This app records user email addresses (PII) so Helm can keep a mailing list in
sync. The analytics app carries only pseudonymous user hashes. The two must
never be joined, so nothing in this package may import from the analytics app.
"""
