"""Download a pinned, SHA-256 verified C compiler (Zig) into work/tools."""

import hashlib
import urllib.request
import zipfile

from workspace import TOOLS_DIRECTORY

ZIG_VERSION = "0.15.2"
ZIG_DIRECTORY_NAME = f"zig-x86_64-windows-{ZIG_VERSION}"
ZIG_ARCHIVE_SHA256 = "3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c"
DOWNLOAD_ROOT = "https://ziglang.org/download/"
HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    TOOLS_DIRECTORY.mkdir(exist_ok=True)
    archive = TOOLS_DIRECTORY / f"{ZIG_DIRECTORY_NAME}.zip"
    if not archive.exists():
        urllib.request.urlretrieve(f"{DOWNLOAD_ROOT}{ZIG_VERSION}/{archive.name}", archive)
    if file_sha256(archive) != ZIG_ARCHIVE_SHA256:
        raise RuntimeError("Compiler archive checksum mismatch")
    compiler = TOOLS_DIRECTORY / ZIG_DIRECTORY_NAME / "zig.exe"
    if not compiler.exists():
        with zipfile.ZipFile(archive) as source:
            source.extractall(TOOLS_DIRECTORY)
    print(compiler)


if __name__ == "__main__":
    main()
