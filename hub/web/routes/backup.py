"""Settings -> Backup: used space and the archive with all data (see archive.py)."""
import asyncio
import contextlib
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from hub.core import archive, storage
from hub.core.i18n import tr
from hub.paths import DATA_DIR
from hub.web.responses import fail

router = APIRouter()


@router.get("/api/storage")
def storage_usage():
    """Space used by the app, the database, photos and logs (folders are walked on every request)."""
    return {"status": "ok", **storage.usage()}


# ---------- Archive: the whole data (database, config.json, photos) as one zip, see archive.py ----------
_archive_lock = asyncio.Lock()       # one archive job at a time: two restores at once would mix their photos


def _remove_file(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


@router.get("/api/archive")
async def download_archive():
    """Builds the archive in a temp file next to the data and sends it; the file is removed after sending."""
    async with _archive_lock:
        fd, path = tempfile.mkstemp(prefix=".download-", suffix=".zip", dir=DATA_DIR)
        os.close(fd)
        try:
            await asyncio.to_thread(archive.make_archive, path)
        except Exception as e:
            _remove_file(path)
            print(f"[ARCHIVE] could not build the archive: {e!r}")
            return Response(tr("archive.err_build"), status_code=500, media_type="text/plain; charset=utf-8")
    return FileResponse(path, media_type="application/zip", filename=archive.archive_name(),
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(_remove_file, path))


@router.post("/api/archive/restore")
async def restore_archive_route(request: Request):
    """The archive comes as the raw request body (no multipart parser needed) and is written to a temp file
    piece by piece, so a large archive never sits in memory."""
    if _archive_lock.locked():
        return fail(tr("archive.err_busy"))
    async with _archive_lock:
        fd, path = tempfile.mkstemp(prefix=".upload-", suffix=".zip", dir=DATA_DIR)
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
            summary = await asyncio.to_thread(archive.restore_archive, path)
        except archive.ArchiveError as e:
            return fail(tr(e.key, **e.params))
        except Exception as e:
            print(f"[ARCHIVE] restore failed: {e!r}")
            return fail(tr("archive.err_restore", error=e.__class__.__name__))
        finally:
            _remove_file(path)
    return {"status": "ok", **summary}
