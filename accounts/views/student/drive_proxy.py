import mimetypes
import re
import io
import queue
import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404

from googleapiclient.http import MediaIoBaseDownload

from accounts.models import Course, EnrolledCourse
from langgraph_agents.services.drive_service import GoogleDriveAuthService

logger = logging.getLogger(__name__)
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class _QueueWriter(io.RawIOBase):
    """
    File-like object for MediaIoBaseDownload that pushes bytes into a queue
    instead of storing the entire file in memory.
    """
    def __init__(self):
        self.q = queue.Queue()
        self.closed_flag = False

    def writable(self):
        return True

    def write(self, b):
        if self.closed_flag:
            return 0
        if b:
            self.q.put(bytes(b))
            return len(b)
        return 0

    def close(self):
        self.closed_flag = True
        super().close()


def _is_enrolled(user, course: Course) -> bool:
    return EnrolledCourse.objects.filter(student=user, course=course).exists()


@login_required
def drive_stream(request, course_id: int, file_id: str):
    """
    Streams a Google Drive file THROUGH Django so the student never navigates to Drive.
    Supports Range requests for video seeking.
    """
    course = get_object_or_404(Course, pk=course_id)
    if not _is_enrolled(request.user, course):
        raise Http404("Enrollment required")

    try:
        service = GoogleDriveAuthService.get_service()

        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size"
        ).execute()

        filename = meta.get("name") or "file"
        mime_type = meta.get("mimeType") or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        size = meta.get("size")
        try:
            size_int = int(size) if size is not None else None
        except Exception:
            size_int = None

        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
        headers = {}
        status_code = 200

        req = service.files().get_media(fileId=file_id)

        # Range support (important for video seeking)
        if range_header:
            m = _RANGE_RE.match(range_header.strip())
            if not m:
                raise Http404("Invalid Range")

            start_s, end_s = m.group(1), m.group(2)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else (size_int - 1 if size_int is not None else None)

            req.headers["Range"] = f"bytes={start}-{end if end is not None else ''}"
            status_code = 206
            headers["Accept-Ranges"] = "bytes"
            if size_int is not None and end is not None:
                headers["Content-Range"] = f"bytes {start}-{end}/{size_int}"
                headers["Content-Length"] = str((end - start) + 1)
        else:
            if size_int is not None:
                headers["Content-Length"] = str(size_int)

        writer = _QueueWriter()
        downloader = MediaIoBaseDownload(writer, req, chunksize=1024 * 1024)

        def stream():
            done = False
            while not done:
                _, done = downloader.next_chunk()
                while not writer.q.empty():
                    yield writer.q.get()

        resp = StreamingHttpResponse(stream(), content_type=mime_type, status=status_code)
        resp["Content-Disposition"] = f'inline; filename="{filename}"'
        for k, v in headers.items():
            resp[k] = v
        return resp

    except Http404:
        raise
    except Exception as e:
        logger.exception("drive_stream failed file_id=%s course_id=%s err=%s", file_id, course_id, e)
        raise Http404("File not available")