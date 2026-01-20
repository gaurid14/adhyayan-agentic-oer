import json
import os
from typing import Optional

from django.conf import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError


# ==============================
# 🔐 Google Drive Authentication
# ==============================
class GoogleDriveAuthService:
    """
    Handles OAuth token loading and Drive service creation.
    Single Responsibility: AUTH ONLY.
    """

    @staticmethod
    def load_credentials() -> Credentials:
        token_file = settings.GOOGLE_TOKEN_FILE

        if not os.path.exists(token_file):
            raise RefreshError("Google Drive token file not found")

        with open(token_file, "r") as f:
            token_data = json.load(f)

        return Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )

    @classmethod
    def get_service(cls):
        creds = cls.load_credentials()
        return build("drive", "v3", credentials=creds)


# ==============================
# 📁 Google Drive Folder Service
# ==============================
class GoogleDriveFolderService:
    """
    Responsible for folder lookup and creation.
    No authentication logic here.
    """

    def __init__(self, service):
        self.service = service

    def get_or_create_folder(
            self,
            folder_name: str,
            parent_id: Optional[str] = None
    ) -> str:
        """
        Returns folder ID. Creates folder if it does not exist.
        """

        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{folder_name}' "
            "and trashed=false"
        )

        if parent_id:
            query += f" and '{parent_id}' in parents"

        result = (
            self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name)"
            )
            .execute()
        )

        folders = result.get("files", [])
        if folders:
            return folders[0]["id"]

        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }

        if parent_id:
            metadata["parents"] = [parent_id]

        folder = (
            self.service.files()
            .create(body=metadata, fields="id")
            .execute()
        )

        return folder["id"]
