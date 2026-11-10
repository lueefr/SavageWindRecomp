#!/usr/bin/env python3
"""Plan versioned portable AppImage releases and build wrapper-only release notes."""

import argparse
import re
import subprocess
from pathlib import Path

TAG_RE = re.compile(r"^portable-v(\d+)\.(\d+)\.(\d+)$")
APPIMAGE = "SavageWind-Portable-x86_64.AppImage"
REPO = "lueefr/SavageWindRecomp"

# Only files that affect the packaged wrapper or its release machinery count.
WRAPPER_FILES = {
    ".github/workflows/wrapper.yml",
    "tools/linux/build_wrapper.sh",
    "tools/linux/wrapper_AppRun",
    "tools/linux/wrapper.desktop",
    "tools/linux/import_game.py",
    "tools/linux/portable_release.py",
    "tools/build_hactool.py",
    "docs/icon/icon_1024.png",
}


def is_wrapper_file(path):
    if path in WRAPPER_FILES:
        return True
    if path.startswith("wrapper/"):
        return not path.startswith("wrapper/tests/") and not path.lower().endswith(
            (".md", ".rst", ".txt")
        )
    if path.startswith(("tools/linux/nsp_vendor/", "tools/hactool/")):
        name = Path(path).name.lower()
        return not name.startswith(("readme", "changelog", "license", "copying")) and not name.endswith(
            (".md", ".rst", ".txt")
        )
    return False


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def parent_tag():
    versions = []
    for tag in git("tag", "--list", "portable-v*").splitlines():
        match = TAG_RE.fullmatch(tag)
        if match:
            versions.append((tuple(map(int, match.groups())), tag))
    if not versions:
        return None, "portable-v0.1.0"
    version, tag = max(versions)
    return tag, f"portable-v{version[0]}.{version[1]}.{version[2] + 1}"


def release_plan():
    previous, tag = parent_tag()
    if previous:
        if subprocess.run(["git", "merge-base", "--is-ancestor", previous, "HEAD"],
                          check=False).returncode:
            raise SystemExit(f"Latest portable release {previous} is not an ancestor of HEAD")
        base = previous
    else:
        # The old rolling preview establishes the baseline for the first versioned release.
        preview = subprocess.run(["git", "rev-parse", "--verify", "-q",
                                  "refs/tags/portable-preview^{commit}"],
                                 capture_output=True, text=True)
        base = "portable-preview" if preview.returncode == 0 and subprocess.run(
            ["git", "merge-base", "--is-ancestor", "portable-preview", "HEAD"],
            check=False).returncode == 0 else git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]
    changed = git("diff", "--name-only", "--no-renames", base, "HEAD").splitlines()
    relevant = sorted(path for path in changed if is_wrapper_file(path))
    return base, tag, relevant


def change_messages(base, limit=50):
    revisions = git("log", "--no-merges", "--format=%H", f"{base}..HEAD").splitlines()
    items = []
    for sha in revisions:
        changed = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        if not any(is_wrapper_file(name) for name in changed):
            continue
        title = git("show", "-s", "--format=%s", sha).replace("\r", " ").replace("\n", " ")
        title = title.replace("[", "\\[").replace("]", "\\]").replace("`", "'")
        items.append(f"- {title} ([{sha[:8]}](https://github.com/{REPO}/commit/{sha}))")
        if len(items) >= limit:
            items.append(f"- Additional changes: [compare commits](https://github.com/{REPO}/compare/{base}...HEAD)")
            break
    return items


def notes(base, tag, image, relevant):
    import hashlib
    h = hashlib.sha256()
    with open(image, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    messages = change_messages(base)
    return "\n".join([
        f"# Savage Wind Portable {tag.removeprefix('portable-')}",
        "",
        "## Changelog (portable wrapper only)",
        "",
        *(messages or ["- Wrapper packaging and release updates."]),
        "",
        "Documentation-only, README, changelog and unrelated upstream changes are excluded.",
        "",
        "## Package",
        "",
        f"- **AppImage:** \`{APPIMAGE}\` (Linux x86-64).",
        f"- **SHA-256:** \`{h.hexdigest()}\`.",
        f"- **Source commit:** \`{git('rev-parse', 'HEAD')}\` (main).",
        f"- **Modified wrapper files:** {len(relevant)}.",
        "- Includes the Qt/GTK portable launcher and NSP/XCI importer; does not contain the game, keys or official game executable.",
        "",
        "Based on the upstream [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp).",
        "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "notes"))
    parser.add_argument("--output", help="GitHub Actions outputs file or Markdown notes destination")
    parser.add_argument("--image", default=f"build/dist/{APPIMAGE}")
    args = parser.parse_args()

    base, tag, relevant = release_plan()
    if args.command == "plan":
        result = f"release={'true' if relevant else 'false'}\ntag={tag}\nbase={base}\n"
        print(f"Wrapper files changed since {base}: {len(relevant)}")
        for file in relevant:
            print(f"  {file}")
        if args.output:
            with open(args.output, "a", encoding="utf-8") as target:
                target.write(result)
        else:
            print(result, end="")
    else:
        if not relevant:
            raise SystemExit("No wrapper changes to release")
        data = notes(base, tag, args.image, relevant)
        if args.output:
            Path(args.output).write_text(data, encoding="utf-8")
        else:
            print(data)


if __name__ == "__main__":
    main()
