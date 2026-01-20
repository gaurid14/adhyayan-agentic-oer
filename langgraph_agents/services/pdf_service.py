import io

from PyPDF2 import PdfReader
from googleapiclient.http import MediaIoBaseDownload

from langgraph_agents.services.drive_service import GoogleDriveAuthService


def download_and_read_pdf(file_id: str) -> str:
    """
    Downloads a PDF from Google Drive and extracts all readable text.
    """
    try:
        service = GoogleDriveAuthService.get_service()

        # 1️⃣ Download file bytes from Google Drive
        request = service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        file_stream.seek(0)

        # 2️⃣ Read PDF text using PyPDF2
        reader = PdfReader(file_stream)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""

        return text.strip()

    except Exception as e:
        print(f"[ERROR] Failed to download or read PDF ({file_id}): {e}")
        return ""
