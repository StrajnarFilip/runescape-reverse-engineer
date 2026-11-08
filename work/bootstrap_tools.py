"""Fetch official, hash-checked analysis tools into this workspace only."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent / "tools"
ROOT.mkdir(exist_ok=True)
TOOLS = [
    ("ghidra_12.2_PUBLIC_20261016.zip", "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.2_build/ghidra_12.2_PUBLIC_20261016.zip", "2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67"),
    ("OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip", "https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.5%2B8/OpenJDK25U-jdk_x64_windows_hotspot_25.0.5_8.zip", "636bdc13dc649ddc7d5921c5c7d23cca6e44fd19d662b45cc9c02135a6c96c42"),
]

def fetch(item):
    name, url, expected = item
    destination = ROOT / name
    if not destination.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "RS3-reader-build"})
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    digest = hashlib.sha256()
    with destination.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("Checksum mismatch: " + name)
    with zipfile.ZipFile(destination) as archive:
        top = archive.namelist()[0].split("/")[0]
        if not (ROOT / top).exists():
            archive.extractall(ROOT)
    print(json.dumps({"tool": name, "sha256": expected, "directory": str(ROOT / top)}), flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(fetch, TOOLS))
