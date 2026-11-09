# Changelog

## 0.3.0 — 2026-11-08

- New `django_helm.mailer` app and `/helm/user_emails` endpoint. It reports verified accounts that were created or activated and accounts that changed email address, with paging.
- Email events older than 90 days are purged each time the endpoint is called.
- Deleting a user deletes that user's recorded email events.
- The response never carries a user id or a Helm hash, and the mailer app never imports from the analytics app, so an email address cannot be tied to analytics.
- `parse_timestamp` is now a shared helper in `django_helm.endpoints`.

## 0.2.0

- Backend endpoints for Helm: user accounts and analytics exclusion list; see README.
