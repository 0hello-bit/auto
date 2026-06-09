# auro-reg

Local automation workspace for:

- owned mail-code service on `127.0.0.1:5050`
- web registration and Sub2API import service on `127.0.0.1:5060`
- Chrome CDP on `127.0.0.1:9222`
- local Sub2API on `127.0.0.1:8080`

This repository is a code/config template snapshot. Real `.env` files, mailbox
accounts, tokens, SQLite databases, logs, screenshots, traces, and virtual
environments are intentionally not committed.

## Local Setup

1. Copy each `.env.example` to `.env` and fill in local secrets.
2. Put mailbox credentials in `owned-mail-code-service/accounts.txt`.
3. Run:

```powershell
.\sync-running-sub2api-to-auro.ps1
.\run-all.ps1
```

Useful desktop shortcut generators:

```powershell
.\create-auro-reg-shortcuts.ps1
.\create-sub2api-shortcuts.ps1
```

Common commands:

```powershell
.\run-all.ps1          # start Chrome + project A + project B; check Sub2API
.\run-all.ps1 -Auto    # start services, sync emails, then run the batch
.\run-all.ps1 -Stop    # stop project A/B Python services only
```

## Rollback

This snapshot should be tagged after commit. To return to it locally:

```powershell
git checkout auro-reg-local-good-2026-06-09
```

If you need to resume normal editing after that, create a branch from the tag.
