"""
Google Drive integration via shared drive.
Secrets required in Streamlit secrets:
  [drive]              — shared drive JSON fields
  shared_drive_id      — shared drive ID whose subfolders are the input and output folders 
  input_folder_id      — folder ID to browse for input CSVs
  output_parent_id     — parent folder ID whose subfolders are upload destinations
"""
import io
import streamlit as st
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


def get_drive_service():
    """Builds the Drive service using the OAuth token from st.login()."""
    try:
        # Streamlit 1.42+ stores the token here after a successful st.login()
        token = st.user.tokens.get("access")
        
        if not token:
            # If no token, we can't build the service
            return None
            
        creds = Credentials(token)
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"Failed to initialize Drive service: {e}")
        return None


# def drive_secrets_configured() -> bool:
#     """Checks if the necessary IDs and Auth settings are in secrets."""
#     try:
#         # Check for both the Auth section and your Drive ID section
#         return (
#             "auth" in st.secrets and
#             "drive" in st.secrets and
#             "shared_drive_id" in st.secrets["drive"] and
#             "input_folder_id" in st.secrets["drive"]
#         )
#     except Exception:
#         return False
    
    
def drive_secrets_configured() -> bool:
    """Checks for the NEW OAuth [auth] section and Drive IDs."""
    return (
        "auth" in st.secrets and 
        "drive" in st.secrets and
        "shared_drive_id" in st.secrets["drive"]
    )


def list_input_files(service) -> list[dict]:
    """List CSV and XLSX files in the configured input folder."""
    if not service: return []
    
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    input_folder_id = st.secrets["drive"]["input_folder_id"]
    
    try:
        q = (
            f"'{input_folder_id}' in parents and trashed=false and ("
            "mimeType='text/csv' or "
            "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or "
            "mimeType='text/plain' or "
            "name contains '.csv'"  # Added for extra safety
            ")"
        )
        results = service.files().list(
            q=q,
            corpora="drive",
            driveId=shared_drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
          role="fileOrganizer",
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
    """List subfolders inside the configured output parent folder, plus the parent itself. Returns [(name, id)]."""
    shared_drive_id = st.secrets["drive"]["shared_drive_id"]
    parent_id = st.secrets["drive"]["output_folder_id"]
    try:
        # Get parent folder name
        parent = service.files().get(
            fileId=parent_id, fields="id, name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        # List subfolders
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
        # Parent folder is always first option
        return [(parent["name"] + " (root)", parent["id"])] + subfolders
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
        supportsAllDrives=True,
    ).execute()
    return uploaded.get("webViewLink", "")
