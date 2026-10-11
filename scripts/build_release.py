#!/usr/bin/env python3
"""Build portable release archives from the repository's tracked source files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = "gpt-converter-server.exe"
EXCLUDED_DIRS = {
    ".git", ".reference", ".test-artifacts", ".venv", "venv", "env",
    "__pycache__", "node_modules", "dist", "build", ".codex", ".idea",
}
EXCLUDED_SUFFIXES = {
    ".exe", ".zip", ".pyc", ".pyo", ".pyd", ".dll", ".spec", ".log", ".tmp",
}
RUNTIME_FILES = {
    "index.html", "styles.css", "app.js", "converter.js", "archive.js",
    "online.js", "icon.svg", "server.py", "requirements.txt", "package.json",
    "启动工具.bat", "start.ps1", "start.sh", "README.md", "LICENSE",
    "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md",
}
REQUIRED_RUNTIME_FILES = RUNTIME_FILES - {
    "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md",
}


def safe_source_file(root: Path, name: str) -> Path | None:
    """Reject generated files, local state, unsafe paths, and symlink targets."""
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or "\\" in name or ":" in name:
        raise ValueError("Git returned an unsafe archive path")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("Git returned an unsafe archive path")
    if any(part.lower() in EXCLUDED_DIRS for part in relative.parts):
        return None
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return None
    if relative.name == ".env" or relative.name.startswith(".env.") and relative.name != ".env.example":
        return None
    path = root.joinpath(*relative.parts)
    # A tracked file deleted in the working tree must not reappear in an archive.
    if not path.is_file():
        return None
    if any(item.is_symlink() for item in (path, *path.parents) if item != root.parent):
        raise ValueError(f"Symlinks cannot be included in release archives: {name}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Source file escapes the repository: {name}") from exc
    return path


def tracked_sources(root: Path) -> dict[str, Path]:
    """Use the Git index as the file inventory, with current working-tree content."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    names = result.stdout.decode("utf-8").split("\0")
    files = {}
    for name in sorted(set(names) - {""}):
        path = safe_source_file(root, name)
        if path is not None:
            files[name] = path
    if not files:
        raise ValueError("No tracked source files found; stage new files with git add first")
    return files


def project_version(root: Path) -> str:
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("package.json must contain a safe semantic version")
    return version


def write_archive(target: Path, files: dict[str, Path]) -> None:
    """Publish only a completed archive; ZIP entries never contain build paths."""
    temporary = target.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, path in sorted(files.items()):
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                    raise ValueError("Unsafe archive member")
                archive.write(path, arcname=name)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def build_executable(root: Path, output_dir: Path) -> Path:
    build_dir = root / ".test-artifacts" / "release-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", Path(EXECUTABLE).stem,
        "--collect-binaries", "curl_cffi", "--collect-data", "certifi",
        "--distpath", str(build_dir / "dist"),
        "--workpath", str(build_dir / "work"),
        "--specpath", str(build_dir / "spec"),
        str(root / "server.py"),
    ], cwd=root, env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(build_dir / "cache")}, check=True)
    executable = build_dir / "dist" / EXECUTABLE
    if not executable.is_file():
        raise ValueError("PyInstaller completed without producing the expected executable")
    output = output_dir / EXECUTABLE
    shutil.copy2(executable, output)
    return output


def build_release(root: Path, *, source_only: bool = False) -> list[Path]:
    if not source_only and sys.platform != "win32":
        raise ValueError("The Windows executable must be built on Windows; use --source-only elsewhere")
    version = project_version(root)
    sources = tracked_sources(root)
    runtime = {
        name: path for name, path in sources.items()
        if name in RUNTIME_FILES or name.startswith(("docs/", "examples/"))
    }
    if not source_only:
        missing = REQUIRED_RUNTIME_FILES - runtime.keys()
        if missing:
            raise ValueError("Required release files are missing or untracked: " + ", ".join(sorted(missing)))
    output_dir = root / "dist"
    output_dir.mkdir(exist_ok=True)
    source_archive = output_dir / f"gpt-converter-source-v{version}.zip"
    write_archive(source_archive, sources)
    artifacts = [source_archive]
    if not source_only:
        runtime[EXECUTABLE] = build_executable(root, output_dir)
        windows_archive = output_dir / f"gpt-converter-windows-v{version}.zip"
        write_archive(windows_archive, runtime)
        artifacts.append(windows_archive)
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-only", action="store_true",
        help="create a source ZIP from Git-tracked/staged files without building an executable",
    )
    args = parser.parse_args()
    try:
        artifacts = build_release(ROOT, source_only=args.source_only)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1
    for artifact in artifacts:
        print(f"Created {artifact.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
