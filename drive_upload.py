"""
Google Drive upload via OAuth2 service account or user OAuth.
For Streamlit Community Cloud, credentials are stored in st.secrets.
"""
import io
import json
import streamlit as st


def get_drive_service():
    """Build a Google Drive API service from Streamlit secrets."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_dict = dict(st.secrets["google_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        return None, str(e)


def list_drive_folders(service):
    """Return list of (name, id) tuples for Drive folders accessible to the service account."""
    try:
        results = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)",
            pageSize=50,
        ).execute()
        folders = results.get("files", [])
        return [(f["name"], f["id"]) for f in folders]
    except Exception as e:
        return []


def upload_to_drive(service, file_bytes: bytes, filename: str, folder_id: str = None) -> str:
    """Upload Excel bytes to Drive. Returns the file URL."""
    from googleapiclient.http import MediaIoBaseUpload

    file_metadata = {"name": filename}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return uploaded.get("webViewLink", "")


def drive_secrets_configured() -> bool:
    return "google_service_account" in st.secrets
