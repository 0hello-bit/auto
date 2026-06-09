"""Owned Mail Verification Code Service.

A small FastAPI service that polls user-owned Outlook/Hotmail mailboxes over
IMAP (XOAUTH2) and exposes received verification codes through an authenticated
HTTP API.

Intended ONLY for mailboxes you own or are explicitly authorized to manage,
and only for your own / authorized-test systems.
"""

__version__ = "1.0.0"
