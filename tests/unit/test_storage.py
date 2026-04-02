import pytest

from app.api.v1.attachments import _detect_mime
from app.core.storage import LocalStorageService

# Magic byte sequences
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 32
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 32  # ZIP magic used by Office formats
EXE_BYTES = b"MZ" + b"\x00" * 32


# --- _detect_mime ---


@pytest.mark.parametrize(
    "data,filename,expected",
    [
        # Magic-byte detection
        (JPEG_BYTES, "photo.jpg", "image/jpeg"),
        (PDF_BYTES, "doc.pdf", "application/pdf"),
        # Extension overrides ZIP magic bytes for Office formats
        (
            ZIP_BYTES,
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            ZIP_BYTES,
            "sheet.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            ZIP_BYTES,
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (ZIP_BYTES, "archive.zip", "application/zip"),
        # Extension fallback (no magic bytes for these types)
        (b"<svg></svg>", "icon.svg", "image/svg+xml"),
        (b"hello world", "readme.txt", "text/plain"),
        (b"a,b,c\n1,2,3", "data.csv", "text/csv"),
        # Unknown type
        (b"random garbage", "file.xyz", None),
        (b"random garbage", "noextension", None),
    ],
)
def test_detect_mime(data, filename, expected):
    assert _detect_mime(data, filename) == expected


def test_detect_mime_exe_contract():
    # EXE is detected by filetype but not in the allowlist.
    # _detect_mime just returns the type; the router decides whether to accept it.
    result = _detect_mime(EXE_BYTES, "program.exe")
    assert result is not None
    assert result != "image/jpeg"


# --- LocalStorageService ---


@pytest.mark.asyncio
async def test_local_storage_upload_creates_file(tmp_path):
    svc = LocalStorageService(base_dir=str(tmp_path))
    await svc.upload_file(b"hello", "subdir/test.txt")
    assert (tmp_path / "subdir" / "test.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_local_storage_upload_creates_parent_dirs(tmp_path):
    svc = LocalStorageService(base_dir=str(tmp_path))
    await svc.upload_file(b"data", "a/b/c/file.bin")
    assert (tmp_path / "a" / "b" / "c" / "file.bin").exists()


@pytest.mark.asyncio
async def test_local_storage_delete_removes_file(tmp_path):
    svc = LocalStorageService(base_dir=str(tmp_path))
    await svc.upload_file(b"content", "to_delete.txt")
    assert (tmp_path / "to_delete.txt").exists()

    await svc.delete_file("to_delete.txt")
    assert not (tmp_path / "to_delete.txt").exists()


@pytest.mark.asyncio
async def test_local_storage_delete_missing_file_is_noop(tmp_path):
    svc = LocalStorageService(base_dir=str(tmp_path))
    # Should not raise
    await svc.delete_file("nonexistent.txt")


def test_local_storage_get_url(tmp_path):
    svc = LocalStorageService(base_dir=str(tmp_path))
    url = svc.get_url("attachments/abc.jpg")
    assert "attachments/abc.jpg" in url
