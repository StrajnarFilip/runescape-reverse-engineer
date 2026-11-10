"""Fetch the official, hash-checked analysis tools (Ghidra and a JDK) into work/tools."""

import concurrent.futures
import hashlib
import json
import urllib.request
import zipfile
from dataclasses import dataclass

from workspace import TOOLS_DIRECTORY

CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
USER_AGENT = "RS3-reader-build"


@dataclass(frozen=True)
class PinnedTool:
    archive_name: str
    url: str
    sha256: str


TOOLS = (
    PinnedTool(
        "ghidra_12.2_PUBLIC_20261016.zip",
        "https://github.com/NationalSecurityAgency/ghidra/releases/download/"
        "Ghidra_12.2_build/ghidra_12.2_PUBLIC_20261016.zip",
        "2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67",
    ),
    PinnedTool(
        "OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip",
        "https://github.com/adoptium/temurin25-binaries/releases/download/"
        "jdk-25.0.5%2B8/OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip",
        "636bdc13dc649ddc7d5921c5c7d23cca6e44fd19d662b45cc9c02135a6c96c42",
    ),
)


def download(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        with destination.open("wb") as output:
            for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                output.write(chunk)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(tool):
    archive = TOOLS_DIRECTORY / tool.archive_name
    if not archive.exists():
        download(tool.url, archive)
    if file_sha256(archive) != tool.sha256:
        raise RuntimeError("Checksum mismatch: " + tool.archive_name)
    with zipfile.ZipFile(archive) as source:
        top_directory = source.namelist()[0].split("/")[0]
        if not (TOOLS_DIRECTORY / top_directory).exists():
            source.extractall(TOOLS_DIRECTORY)
    report = {
        "tool": tool.archive_name,
        "sha256": tool.sha256,
        "directory": str(TOOLS_DIRECTORY / top_directory),
    }
    print(json.dumps(report), flush=True)


def main():
    TOOLS_DIRECTORY.mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(TOOLS)) as pool:
        list(pool.map(fetch, TOOLS))


if __name__ == "__main__":
    main()
