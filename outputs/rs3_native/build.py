"""Build the Windows x64 C library, C example and native tests.

Use an installed cl/clang/gcc or --cc path/to/zig.exe. Does not download tools.
Example: python build.py --cc ../../work/tools/zig-x86_64-windows-0.15.2/zig.exe --test
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", help="C compiler executable (cl, clang, gcc or zig)")
    parser.add_argument("--test", action="store_true", help="Build and run deterministic native tests")
    parser.add_argument("--cache-root", type=Path, default=Path(r"C:\ProgramData\Jagex\RuneScape"))
    parser.add_argument("--skip-catalog", action="store_true", help="Build without regenerating local item/ability metadata")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This collector targets Windows x64")
    compiler = args.cc or next((shutil.which(c) for c in ("cl", "clang", "gcc", "zig") if shutil.which(c)), None)
    if compiler is None:
        parser.error("No C compiler found; install MSVC/Clang/GCC/Zig or pass --cc")
    compiler = str(Path(compiler).resolve()) if Path(compiler).is_file() else compiler
    name = Path(compiler).stem.lower()
    build = ROOT/"build"
    build.mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(ROOT/"generate_profile.py")], check=True)
    if not args.skip_catalog:
        if (args.cache_root/"js5-19.jcache").is_file():
            subprocess.run([sys.executable, str(ROOT/"generate_catalog.py"), "--cache-root", str(args.cache_root)], check=True)
        else:
            print("Game cache not found; IDs remain readable, but metadata requires generate_catalog.py", file=sys.stderr)
    msvc = name in ("cl", "clang-cl")

    def compile_source(source, target, shared=False, extra=None):
        if msvc:
            command = [compiler, "/nologo", "/std:c11", "/O2", "/W4", "/WX", "/D_CRT_SECURE_NO_WARNINGS",
                       "/I"+str(ROOT/"include"), "/I"+str(ROOT/"src"), str(source), "/Fe:"+str(target)]
            if shared:
                command.append("/LD")
            command += extra or []
            command += ["/link", "bcrypt.lib"]
        else:
            command = [compiler]
            if name == "zig":
                command += ["cc", "-target", "x86_64-windows-gnu"]
            command += ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT/"include"), "-I", str(ROOT/"src")]
            if shared:
                command.append("-shared")
            command += [str(source), "-lbcrypt", "-o", str(target)] + (extra or [])
        print("Building "+target.name, flush=True)
        subprocess.run(command, check=True, cwd=build)

    compile_source(ROOT/"src"/"rs3_native.c", build/"rs3_native.dll", shared=True)
    if args.test:
        compile_source(ROOT/"tests"/"test_native.c", build/"test_native.exe")
        subprocess.run([str(build/"test_native.exe")], check=True)
    # The example resolves the DLL at runtime, so it works with either toolchain.
    compile_source(ROOT/"examples"/"read.c", build/"read.exe")


if __name__ == "__main__":
    main()
