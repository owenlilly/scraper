"""The raw-response archive: bodies stored once each, compressed, named by the sha256 of what the server sent."""

import hashlib
import os
import tempfile
from compression import zstd
from pathlib import Path


class FilesystemBlobStore:
    """A directory of zstd files, `root/ab/cd/<sha256>.zst`. Identical bodies are one file, so re-fetching an unchanged page
    costs no space. Swap this class for an object-store one without touching the repositories."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256[2:4] / f"{sha256}.zst"

    def put(self, body: bytes) -> str:
        sha256 = hashlib.sha256(body).hexdigest()
        path = self._path(sha256)
        if path.exists():
            return sha256
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(zstd.compress(body))
            os.replace(tmp, path)  # atomic, so a reader never sees half a file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha256

    def get(self, sha256: str) -> bytes:
        return zstd.decompress(self._path(sha256).read_bytes())
