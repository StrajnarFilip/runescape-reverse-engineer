"""Download a pinned, SHA-256 verified C compiler into the local tools directory."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

version = "0.15.2"
name = "zig-x86_64-windows-"+version
expected = "3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c"
root = Path(__file__).resolve().parent / "tools"
root.mkdir(exist_ok=True)
archive = root / (name+".zip")
if not archive.exists():
    urllib.request.urlretrieve("https://ziglang.org/download/"+version+"/"+archive.name, archive)
with archive.open("rb") as source:
    digest = hashlib.sha256()
    for block in iter(lambda:source.read(1024*1024), b""):
        digest.update(block)
if digest.hexdigest() != expected:
    raise RuntimeError("Compiler archive checksum mismatch")
if not (root/name/"zig.exe").exists():
    with zipfile.ZipFile(archive) as source:
        source.extractall(root)
print(root/name/"zig.exe")
