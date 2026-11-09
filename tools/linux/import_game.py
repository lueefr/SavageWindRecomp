#!/usr/bin/env python3
"""Import the player's base NSP/XCI and update NSP into the existing Savage Wind layout.

No game files or keys are distributed. The pinned hactool merges the update's
BKTR section with the base Program NCA locally on the player's computer.
"""
import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "nsp_vendor"))
import extract_exefs as ex  # noqa: E402
from extract_romfs import index_romfs  # noqa: E402
from extract_update_romfs import copy_range, hactool_prod_keys  # noqa: E402

UNSIGNED = re.compile(r"\s*(?:Fixed-Key Signature|NPDM Signature) \(FAIL\)")
FAILURE = re.compile(r"\(FAIL\)|\(INVALID\)|Failed to|Error:")
KEYS = re.compile(r"[0-9a-fA-F]{32,}")


def progress(message):
    print(message, flush=True)


def inspect_package(path, keys, is_update):
    """Locate and validate the Program NCA before writing anything."""
    with path.open("rb") as stream:
        kind, entries = ex.container_entries(stream, path.stat().st_size)
        ex.require(not is_update or kind == "nsp", "The update must be an NSP file")
        tickets = ex.common_tickets(stream, entries)
        record, header = ex.program_nca(ex.nca_inventory(stream, entries, keys), update=is_update)
        rights = header[0x230:0x240]
        ex.require(rights == bytes(16) or rights in tickets, "Missing common ticket in game package")
        progress(f"Inspected {'update' if is_update else 'base'} {kind.upper()} package: "
                 f"{len(entries)} entries, Program NCA {record['size']:,} bytes.")
        return record, header, tickets


def extract_code(update, keys_path, out):
    """Use the reference extractor's ExeFS hash verification in-process."""
    argv = sys.argv
    try:
        sys.argv = ["extract_exefs", str(update), "--keys", str(keys_path), "--update", "--output", str(out)]
        with contextlib.redirect_stdout(io.StringIO()):
            ex.main()
    finally:
        sys.argv = argv
    ex.require((out / "main").is_file() and (out / "main.npdm").is_file(), "Incomplete update ExeFS")
    for entry in sorted(out.iterdir()):
        if entry.is_file():
            progress(f"Verified ExeFS file: {entry.name} ({entry.stat().st_size:,} bytes).")


def merge(base, update, keys_path, hactool, target, base_info, update_info, tickets):
    """Merge BKTR and check all six IVFC levels, as in the TotK installer."""
    base_record, _ = base_info
    update_record, update_header = update_info
    sections = [i for i in range(4) if update_header[0x400 + i * 512 + 4] == 4]
    ex.require(len(sections) == 1, "Expected exactly one update BKTR section")
    progress(f"Update BKTR section: {sections[0]}.")
    with tempfile.TemporaryDirectory(prefix="sw-bktr-", dir=target.parent) as folder:
        work = Path(folder)
        title_keys = work / "title.keys"
        fd = os.open(title_keys, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as output:
            for rights, key in tickets.items():
                output.write(f"{rights.hex()} = {key.hex()}\n")
        prod_keys = work / "hactool.keys"
        hactool_prod_keys(keys_path, prod_keys)
        base_nca, update_nca = work / "base.nca", work / "update.nca"
        progress("Copying the base Program NCA...")
        copy_range(base, ex.record_offset(base_record), base_record["size"], base_nca)
        progress("Copying the update Program NCA...")
        copy_range(update, ex.record_offset(update_record), update_record["size"], update_nca)
        progress("Merging the update RomFS and verifying hashes...")
        command = [str(hactool), "-x", "-y", "--disablekeywarns", "--suppresskeys", "-k", str(prod_keys),
                   f"--basenca={base_nca}", f"--section{sections[0]}={target}", str(update_nca)]
        # Do not let unrelated keys in ~/.switch override the selected key file.
        env = dict(os.environ, MNG_TITLE_KEYS=str(title_keys), HOME=str(work))
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace")
        log = KEYS.sub("<redacted>", result.stdout + result.stderr)
        checked = "\n".join(line for line in log.splitlines() if not UNSIGNED.match(line))
        if result.returncode or FAILURE.search(checked):
            raise ValueError(f"hactool BKTR merge failed (exit {result.returncode}): {checked[-2500:]}")
        for level in range(6):
            ex.require(bool(re.search(rf"Level {level} \(GOOD\)", log)),
                       f"hactool did not verify IVFC level {level}")
            progress(f"Verified RomFS integrity: IVFC level {level}/5 (GOOD).")
        ex.require(target.is_file() and target.stat().st_size > 0, "hactool did not produce a RomFS image")


def materialize(image, output):
    """Stream the merged RomFS into the directory tree expected by the launcher."""
    entries = index_romfs(image)
    ex.require(bool(entries), "Merged RomFS is empty")
    output.mkdir(parents=True)
    with image.open("rb") as source:
        for index, entry in enumerate(entries):
            rel = PurePosixPath(entry["path"])
            ex.require(not rel.is_absolute() and rel.parts and
                       all(part not in ("", ".", "..") for part in rel.parts), "Unsafe RomFS path")
            dest = output.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            ex.require(not dest.exists(), "Duplicate RomFS output filename")
            source.seek(entry["offset"])
            remaining = entry["size"]
            with dest.open("xb") as target:
                while remaining:
                    block = source.read(min(8 * 1024 * 1024, remaining))
                    ex.require(bool(block), "Truncated merged RomFS")
                    target.write(block)
                    remaining -= len(block)
            progress(f"RomFS file {index + 1}/{len(entries)}: {rel} ({entry['size']:,} bytes)")
            if index % 2000 == 0:
                progress(f"Extracted {index}/{len(entries)} RomFS files...")
    progress(f"Extracted {len(entries):,} RomFS files.")


def import_game(base, update, keys_path, hactool, destination):
    for path in (base, update, keys_path):
        ex.require(path.is_file(), f"File not found: {path}")
    ex.require(hactool.is_file(), "The bundled hactool executable is missing")
    ex.require(not destination.exists() and not destination.is_symlink(),
               "Destination already exists; remove an earlier incomplete import before retrying")
    ex.require(destination.parent.is_dir(), "Destination parent folder is missing")
    ex.require(base.resolve() != update.resolve(), "Base game and update must be different files")
    keys = ex.load_keys(keys_path)
    ex.require("header_key" in keys, "prod.keys is missing header_key")
    progress("Checking base and update packages...")
    base_record, base_header, base_tickets = inspect_package(base, keys, False)
    update_record, update_header, update_tickets = inspect_package(update, keys, True)
    tickets = dict(base_tickets)
    for rights, key in update_tickets.items():
        ex.require(rights not in tickets or tickets[rights] == key, "Conflicting common tickets")
        tickets[rights] = key
    required = base_record["size"] + update_record["size"] + 2 * (18 << 30)
    available = shutil.disk_usage(destination.parent).free
    progress(f"Checking temporary disk space: {available // (1 << 30)} GiB free, "
             f"{required // (1 << 30) + 1} GiB required.")
    ex.require(available > required, f"Not enough free space: need about {required // (1 << 30) + 1} GiB")
    destination.mkdir(mode=0o700)
    try:
        progress("Extracting and verifying update ExeFS...")
        extract_code(update, keys_path, destination / "exefs")
        merged = destination / "merged.romfs"
        merge(base, update, keys_path, hactool, merged,
              (base_record, base_header), (update_record, update_header), tickets)
        progress("Expanding the merged RomFS...")
        materialize(merged, destination / "romfs")
        merged.unlink()
        progress("Removed the temporary merged RomFS image.")
        progress("Game files ready for Savage Wind setup.")
    except BaseException:
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="your base NSP/XCI")
    parser.add_argument("--update", type=Path, required=True, help="your update NSP")
    parser.add_argument("--keys", type=Path, required=True, help="your prod.keys")
    parser.add_argument("--hactool", type=Path, required=True, help="bundled hactool executable")
    parser.add_argument("--dest", type=Path, required=True, help="empty staging directory")
    args = parser.parse_args()
    try:
        import_game(args.base, args.update, args.keys, args.hactool, args.dest)
    except (OSError, ValueError) as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
