"""
Google Service Account operations.

Handles authentication using a service account JSON key file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Lazy imports for google libraries
def _lazy_import_service_account_credentials():
    from google.oauth2.service_account import Credentials
    return Credentials

def _lazy_import_build():
    from googleapiclient.discovery import build
    return build

# Assumes the service account key file is in the `app` directory
SERVICE_ACCOUNT_FILE = str(Path(__file__).resolve().parent.parent / "service_account.json")

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

def _get_server_credentials():
    """Loads service account credentials."""
    Credentials = _lazy_import_service_account_credentials()
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account key file not found at {SERVICE_ACCOUNT_FILE}. "
            "Please download it from Google Cloud Console and place it there."
        )
    return Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=DRIVE_SCOPES
    )

def build_server_drive_service() -> Any:
    """Builds a Drive service client authenticated as the service account."""
    build = _lazy_import_build()
    credentials = _get_server_credentials()
    return build("drive", "v3", credentials=credentials)

def build_server_docs_service() -> Any:
    """Builds a Docs service client authenticated as the service account."""
    build = _lazy_import_build()
    credentials = _get_server_credentials()
    return build("docs", "v1", credentials=credentials)
