"""
Google Drive integration via Service Account + Shared Drive.
Secrets required:
  [google_service_account]  — service account JSON fields
  [drive]
    shared_drive_id         — Shared Drive ID
    input_folder_id         — folder to browse for input CSVs
    output_folder_id        — folder whose subfolders are upload destinations
"""
import io
import streamlit as st


def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_dict = dict(st.secrets["google_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        return None


def drive_secrets_configured() -> bool:
    try:
        return (
            "google_service_account" in st.secrets and
            "drive" in st.secrets and
            "shared_drive_id" in st.secrets["drive"] and
            "input_folder_id" in st.secrets["drive"] and
            "output_folder_id" in st.secrets["drive"]
        )
    except Exception:
        return False



def list_input_subfolders(service) -> list[tuple[str, str]]:
    """Return input parent folder + its subfolders as browsing options."""
    if not service:
        return []
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    parent_id = st.secrets["drive"]["input_folder_id"]
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
        st.error(f"Could not access input folder: {e}")
        return []


def list_files_in_folder(service, folder_id: str) -> list[dict]:
    """List CSV and XLSX files in a specific folder."""
    if not service:
        return []
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    try:
        q = (
            f"'{folder_id}' in parents and trashed=false and ("
            "mimeType='text/csv' or "
            "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
            "mimeType='text/plain' or "
            "name contains '.csv'"
            ")"
        )
        results = service.files().list(
            q=q,
            corpora="drive",
            driveId=shared_drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=100,
        ).execute()
        return results.get("files", [])
    except Exception as e:
        st.error(f"Could not list files: {e}")
        return []

def list_input_files(service) -> list[dict]:
    if not service:
        return []
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    input_folder_id = st.secrets["drive"]["input_folder_id"]
    try:
        q = (
            f"'{input_folder_id}' in parents and trashed=false and ("
            "mimeType='text/csv' or "
            "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
            "mimeType='text/plain' or "
            "name contains '.csv'"
            ")"
        )
        results = service.files().list(
            q=q,
            corpora="drive",
            driveId=shared_drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=100,
        ).execute()
        return results.get("files", [])
    except Exception as e:
        st.error(f"Could not list Drive files: {e}")
        return []


def download_drive_file(service, file_id: str) -> bytes:
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
