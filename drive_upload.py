"""
Google Drive integration via OAuth (st.login) + Shared Drive.
Secrets required in Streamlit secrets:
  [auth]               — OAuth config (client_id, client_secret, redirect_uri)
  [drive]
    shared_drive_id    — Shared Drive ID
    input_folder_id    — folder to browse for input CSVs
    output_folder_id   — parent folder whose subfolders are upload destinations
"""
import io
import streamlit as st
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


def get_drive_service():
    """Build Drive service from st.login() OAuth token."""
    try:
        token = st.user.tokens.get("access")
        if not token:
            return None
        creds = Credentials(
            token=token,
            client_id=st.secrets["auth"]["client_id"],
            client_secret=st.secrets["auth"]["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"Drive auth error: {e}")
        return None


def drive_secrets_configured() -> bool:
    try:
        return (
            "auth" in st.secrets and
            "drive" in st.secrets and
            "shared_drive_id" in st.secrets["drive"] and
            "input_folder_id" in st.secrets["drive"] and
            "output_folder_id" in st.secrets["drive"]
        )
    except Exception:
        return False


def list_input_files(service) -> list[dict]:
    """List CSV and XLSX files in the configured input folder."""
    if not service:
        return []
    input_folder_id = st.secrets["drive"]["input_folder_id"]
    try:
        # Simplified call — no corpora/driveId to test basic API access first
        results = service.files().list(
            q=f"'{input_folder_id}' in parents and trashed=false",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="files(id, name, mimeType, modifiedTime)",
            pageSize=20,
        ).execute()
        return results.get("files", [])
    except Exception as e:
        st.error(f"Could not list Drive files: {e}")
        return []


def download_drive_file(service, file_id: str) -> bytes:
    """Download a file from Drive by ID, returns raw bytes."""
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer.read()


def list_output_subfolders(service) -> list[tuple[str, str]]:
    """Return parent folder + its subfolders as upload destinations."""
    if not service:
        return []
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    parent_id = st.secrets["drive"]["output_folder_id"]
    try:
        parent = service.files().get(
            fileId=parent_id, fields="id, name",
            supportsAllDrives=True,
        ).execute()
        q = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=q,
            corpora="drive",
            driveId=shared_drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="files(id, name)",
            orderBy="name",
            pageSize=100,
        ).execute()
        subfolders = [(f["name"], f["id"]) for f in results.get("files", [])]
        return [(parent["name"] + " (root)", parent["id"])] + subfolders
    except Exception as e:
        st.error(f"Could not access output folder: {e}")
        return []


def upload_to_drive(service, file_bytes: bytes, filename: str, folder_id: str) -> str:
    """Upload Excel bytes to a Shared Drive folder. Returns the webViewLink."""
    from googleapiclient.http import MediaIoBaseUpload
    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return uploaded.get("webViewLink", "")
