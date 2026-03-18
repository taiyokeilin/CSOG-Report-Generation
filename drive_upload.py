"""
Google Drive integration via service account.
Secrets required in Streamlit secrets:
  [google_service_account]   — service account JSON fields
  drive_input_folder_id      — folder ID to browse for input CSVs
  drive_output_parent_id     — parent folder ID whose subfolders are upload destinations
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
    except Exception:
        return None


def drive_secrets_configured() -> bool:
    try:
        return (
            "google_service_account" in st.secrets
            and "drive_input_folder_id" in st.secrets
            and "drive_output_parent_id" in st.secrets
        )
    except Exception:
        return False


def list_input_files(service) -> list[dict]:
    """List CSV and XLSX files in the configured input folder."""
    folder_id = st.secrets["drive_input_folder_id"]
    try:
        q = (
            f"'{folder_id}' in parents and trashed=false and ("
            "mimeType='text/csv' or "
            "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
            "mimeType='text/plain'"
            ")"
        )
        results = service.files().list(
            q=q,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=100,
        ).execute()
        return results.get("files", [])
    except Exception as e:
        st.error(f"Could not list Drive files: {e}")
        return []


def download_drive_file(service, file_id: str) -> bytes:
    """Download a file from Drive by ID, returns raw bytes."""
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    from googleapiclient.http import MediaIoBaseDownload
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer.read()


def list_output_subfolders(service) -> list[tuple[str, str]]:
    """Return the configured output folder itself as the only upload destination."""
    parent_id = st.secrets["drive_output_parent_id"]
    try:
        result = service.files().get(
            fileId=parent_id, fields="id, name"
        ).execute()
        return [(result["name"], result["id"])]
    except Exception as e:
        st.error(f"Could not access output folder: {e}")
        return []


def upload_to_drive(service, file_bytes: bytes, filename: str, folder_id: str) -> str:
    """Upload Excel bytes to a Drive folder. Returns the webViewLink."""
    from googleapiclient.http import MediaIoBaseUpload
    file_metadata = {
        "name": filename,
        "parents": [folder_id],
    }
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
