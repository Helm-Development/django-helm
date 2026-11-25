# Changelog

## 0.4.0 — unreleased

- New `django_helm.mailer` app and `/helm/user_emails` endpoint. It reports verified accounts that were created or activated and accounts that changed email address, with paging.
- Email events older than 90 days are purged each time the endpoint is called.
- Deleting a user deletes that user's recorded email events.
- The response never carries a user id or a Helm hash, and the mailer app never imports from the analytics app, so an email address cannot be tied to analytics.
- `parse_timestamp` is now a shared helper in `django_helm.endpoints`.
- A system check, `django_helm.E001`, reports a user model without `email`, `is_active` or `date_joined` when `django_helm.mailer` is installed.
- `helm_endpoint` accepts `json_dumps_params`; the user emails endpoint sends UTF-8 JSON with `ensure_ascii=False`.

## 0.3.0 — 2026-11-13

- `/helm/social_posts` endpoint (HELM-414). Your app supplies posts through `social_posts_provider`; the SDK validates them and serves them with paging; see README.

## 0.2.0

- Backend endpoints for Helm: user accounts and analytics exclusion list; see README.
