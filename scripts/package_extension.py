#!/usr/bin/env python3
"""Build deterministic, installer-compatible Valkey target archives (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import time
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

EXTENSION_ID = "flowsteward.valkey"
VERSION = "1.1.14"
TOP_FOLDER = "flowsteward_valkey"
SOURCE_DATE_EPOCH = 1793908560
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MACHINES = {"linux-amd64": 62, "linux-arm64": 183}
# Add files to this reviewed list when they need to ship in the package.
PAYLOAD_FILES = (
    "toolbox-provenance.json",
    "SHA256SUMS",
    "BRAND-ASSETS.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "commands.py",
    "contracts/connection_types.yaml",
    "contracts/operation_manifest.yaml",
    "contracts/step_ui_manifest.yaml",
    "datasets.py",
    "docs/workflow-actions.md",
    "extension-descriptor.yaml",
    "extension.yaml",
    "health.py",
    "icon.png",
    "main.py",
    "pyproject.toml",
    "requirements-dev.txt",
    "scripts/build-config.json",
    "scripts/build_inside.sh",
    "scripts/build_toolbox.sh",
    "scripts/go-toolchain-selected.json",
    "scripts/go-toolchain-sources.json",
    "scripts/install-go-toolchain.sh",
    "scripts/native_build_info.py",
    "scripts/package_extension.py",
    "scripts/patches/0000-approved-prebuilt-profiles.patch",
    "scripts/patches/0000-security-baseline.patch",
    "scripts/patches/0002-flowsteward-key-value-safety.patch",
    "scripts/patches/0003-flowsteward-resp-budget.patch",
    "scripts/provenance.py",
    "scripts/security-floor.json",
    "tests/test_contracts.py",
    "tests/test_datasets.py",
    "tests/test_google_api_floor.py",
    "tests/test_live.py",
    "tests/test_native_build.py",
    "tests/test_native_registrations.py",
    "tests/test_package.py",
    "tests/test_runtime.py",
    "tests/test_security_floor.py",
    "tests/test_toolbox.py",
    "toolbox.py",
    "ui/actions/actions.yaml",
    "ui/components/about.yaml",
    "ui/components/valkey_connection_form.yaml",
    "ui/components/valkey_state_connection_form.yaml",
    "ui/pages/about.yaml",
    "ui/pages/connection.yaml",
    "ui/pages/support.yaml",
    "ui/queries/queries.yaml",
    "ui/ui_manifest.yaml",
    "validation.py",
)
PAYLOAD_DIRECTORIES = frozenset(
    {"bin", *(name.split("/")[0] for name in PAYLOAD_FILES if "/" in name)}
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 64), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_paths(root: Path, target: str) -> list[Path]:
    paths = [root / name for name in PAYLOAD_FILES]
    paths.append(root / "bin" / target / "toolbox")
    return sorted(path for path in paths if path.exists() or path.is_symlink())


@contextmanager
def _open_directory(root_fd: int, parts: tuple[str, ...]):
    # Relative descriptor opens preserve the directory identity across renames.
    with ExitStack() as stack:
        parent = root_fd
        for part in parts:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, parent)
        yield parent


def _stage_payload(root: Path, stage: Path, target: str) -> None:
    binary = Path("bin") / target / "toolbox"
    remaining = MAX_UNCOMPRESSED_BYTES
    paths = [path.relative_to(root) for path in _payload_paths(root, target)]
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with _open_directory(root_fd, binary.parts[:-1]) as native_dir:
            if set(os.listdir(native_dir)) != {binary.name}:
                raise ValueError("Target directory must contain exactly one native executable")
        for relative in paths:
            with _open_directory(root_fd, relative.parts[:-1]) as parent:
                fd = os.open(
                    relative.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                with os.fdopen(fd, "rb") as stream:
                    file_stat = os.fstat(stream.fileno())
                    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                        raise ValueError(
                            f"Package input is not an independent regular file: {relative}"
                        )
                    if file_stat.st_size > remaining:
                        raise ValueError(
                            "Extension archive exceeds installer uncompressed-size limit"
                        )
                    data = stream.read(remaining + 1)
                if len(data) > remaining:
                    raise ValueError("Extension archive exceeds installer uncompressed-size limit")
                remaining -= len(data)
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                destination.chmod(stat.S_IMODE(file_stat.st_mode) & 0o777)
    except OSError as exc:
        raise ValueError("Unable to read regular files from the reviewed release payload") from exc
    finally:
        os.close(root_fd)


def _native_metadata(root: Path, target: str) -> dict[str, str]:
    binary = root / "bin" / target / "toolbox"
    if any(path.is_symlink() for path in (root / "bin", binary.parent, binary)):
        raise ValueError("Native executable and parent directories must not be symlinks")
    if not binary.is_file() or stat.S_IMODE(binary.stat().st_mode) != 0o755:
        raise ValueError("Native executable must exist with mode 0755")
    with binary.open("rb") as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:5] != b"\x7fELF\x02" or header[5] not in (1, 2):
        raise ValueError("Native executable must be a valid ELF64 binary")
    machine = (
        int.from_bytes(header[18:20], "little")
        if header[5] == 1
        else int.from_bytes(header[18:20], "big")
    )
    if machine != MACHINES[target]:
        raise ValueError("Native executable ELF machine does not match runtime target")
    provenance_path = root / "toolbox-provenance.json"
    if provenance_path.is_symlink():
        raise ValueError("Provenance must not be a symlink")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    record = provenance["binaries"][target]
    digest = sha256(binary)
    if record.get("sha256") != digest or record.get("size") != binary.stat().st_size:
        raise ValueError("Native executable SHA-256 or size does not match provenance")
    # Read the actual ELF build information; truthful old provenance cannot bypass the floor.
    helper_path = Path(__file__).resolve().with_name("native_build_info.py")
    spec = importlib.util.spec_from_file_location("native_security_preflight", helper_path)
    if spec is None or spec.loader is None:
        raise ValueError("Native security preflight is unavailable")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.verify_security(root, target, provenance)
    return {"path": f"bin/{target}/toolbox", "sha256": digest}


def _write_entry(archive: zipfile.ZipFile, relative: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(f"{TOP_FOLDER}/{relative}", time.gmtime(SOURCE_DATE_EPOCH)[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=9)


def package_extension(root: Path, target: str, out_dir: Path) -> Path:
    """Verify a native payload, then atomically publish one target-specific ZIP."""
    if target not in MACHINES:
        raise ValueError("Unsupported runtime target")
    root = root.resolve(strict=True)
    out_dir = out_dir.resolve()
    if out_dir == root:
        raise ValueError("Output directory must differ from the extension root")
    if out_dir.is_relative_to(root) and out_dir.relative_to(root).parts[0] in PAYLOAD_DIRECTORIES:
        raise ValueError("Output directory must not overlap release payload directories")
    with tempfile.TemporaryDirectory(prefix="extension-payload-") as directory:
        stage = Path(directory)
        _stage_payload(root, stage, target)
        return _write_archive(stage, target, out_dir)


def _write_archive(root: Path, target: str, out_dir: Path) -> Path:
    native = _native_metadata(root, target)
    paths = _payload_paths(root, target)
    for required in (
        "LICENSE",
        "NOTICE",
        "toolbox-provenance.json",
        "SHA256SUMS",
        "scripts/go-toolchain-selected.json",
        "extension.yaml",
        "extension-descriptor.yaml",
    ):
        if root / required not in paths:
            raise ValueError(f"Required release file missing: {required}")
    metadata = (
        json.dumps({"runtime_target": target, "native_executable": native}, sort_keys=True).encode()
        + b"\n"
    )
    if sum(path.stat().st_size for path in paths) + len(metadata) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Extension archive exceeds installer uncompressed-size limit")
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{EXTENSION_ID}-{VERSION}-{target}.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=out_dir)
    os.close(descriptor)
    pending = Path(temporary)
    try:
        with zipfile.ZipFile(pending, "w") as archive:
            for path in paths:
                relative = path.relative_to(root).as_posix()
                data = path.read_bytes()
                if data.startswith(b"\x7fELF") and relative != native["path"]:
                    raise ValueError("Package must contain exactly one native executable")
                if (
                    relative == native["path"]
                    and hashlib.sha256(data).hexdigest() != native["sha256"]
                ):
                    raise ValueError("Native executable changed during packaging")
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                _write_entry(archive, relative, data, mode)
            _write_entry(archive, ".fs-package.yaml", metadata, 0o644)
        if pending.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Extension archive exceeds installer compressed-size limit")
        with zipfile.ZipFile(pending) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Extension archive exceeds installer uncompressed-size limit")
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    return destination


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-target", required=True, choices=sorted(MACHINES))
    parser.add_argument("--out-dir", type=Path, default=root / "dist")
    args = parser.parse_args()
    try:
        archive = package_extension(root, args.runtime_target, args.out_dir)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"Packaging failed: {error}\n")
    print(f"[PASS] packed: {archive}")
    print(f"[PASS] sha256: {sha256(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
