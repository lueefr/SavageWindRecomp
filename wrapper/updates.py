"""Per-source startup update checks with short, linked GitHub changelogs."""
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .core import InstallError, fetch_latest, tls_context
from .pr_build import SHA, validated

REPOSITORY = "Revivedx/SavageWindRecomp"
API = "https://api.github.com/repos/" + REPOSITORY
SITE = "https://github.com/" + REPOSITORY
PREFERENCES = "update-prefs.json"


def _github_json(path):
    request = urllib.request.Request(
        API + path,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "SavageWindPortable/1.0"})
    with urllib.request.urlopen(request, timeout=12, context=tls_context()) as response:
        return json.load(response)


def _anchor(url, text):
    # Links in native dialogs only target this project's GitHub pages.
    if not isinstance(url, str) or not url.startswith(SITE + "/"):
        return html.escape(str(text))
    return '<a href="' + html.escape(url, quote=True) + '">' + html.escape(str(text)) + '</a>'


def _release_notes(body):
    if not isinstance(body, str):
        return []
    notes = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("!", "|", "<!--", "&#")):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"[*_\x60]+", "", line)
        line = line.strip()
        if line:
            notes.append(line[:200])
        if len(notes) >= 4:
            break
    return notes


def _commit_changes(before, after):
    """Return a few real commit messages, never guess missing change details."""
    if not (isinstance(before, str) and isinstance(after, str) and
            SHA.fullmatch(before) and SHA.fullmatch(after) and before != after):
        return []
    try:
        data = _github_json("/compare/" + before + "..." + after)
    except (urllib.error.URLError, OSError, ValueError, InstallError):
        return []
    commits = data.get("commits") or []
    result = []
    for item in commits[-4:]:
        sha = item.get("sha", "")
        message = (item.get("commit") or {}).get("message", "").splitlines()
        if not SHA.fullmatch(sha) or not message:
            continue
        title = message[0][:150].strip()
        if title:
            result.append(_anchor(SITE + "/commit/" + sha, title))
    return result


def _changes_html(title, paragraphs, changes):
    pieces = ["<b>" + html.escape(title) + "</b>"]
    pieces.extend("<p>" + part + "</p>" for part in paragraphs)
    if changes:
        pieces.append("<ul>" + "".join("<li>" + change + "</li>"
                                       for change in changes[:8]) + "</ul>")
    return "\n".join(pieces)


def check_update(state):
    """Return update metadata without changing files, state or user settings."""
    source = state.get("runtime_source", "official")
    if source == "official":
        latest = fetch_latest()
        if state.get("official_digest") == latest["digest"]:
            return None
        tag = latest["tag"]
        page = SITE + "/releases/tag/" + urllib.parse.quote(tag, safe="")
        notes = []
        try:
            info = _github_json("/releases/tags/" + urllib.parse.quote(tag, safe=""))
            notes = _release_notes(info.get("body"))
        except (urllib.error.URLError, OSError, ValueError, InstallError):
            pass
        details = [html.escape(note) for note in notes]
        if not details:
            details = ["Nova release oficial publicada; detalhes no link da release."]
        description = _changes_html(
            "Atualização oficial disponível",
            ["Release: " + _anchor(page, tag),
             "Versão instalada: " + html.escape(str(state.get("official_tag") or "desconhecida"))],
            details)
        return {"source": "official", "release": latest,
                "title": "Savage Wind — Atualização oficial",
                "html": description, "revision": latest["digest"]}

    if source not in ("test", "custom"):
        raise InstallError("Unknown runtime source; startup update check was skipped.")

    branch = _github_json("/branches/test")
    current = (branch.get("commit") or {}).get("sha")
    if not isinstance(current, str) or not SHA.fullmatch(current):
        raise InstallError("GitHub returned an invalid test branch revision.")

    if source == "test":
        before = state.get("test_commit")
        if before == current:
            return None
        changes = _commit_changes(before, current)
        if not changes:
            changes = ["Alterações detectadas; consulte o histórico da branch."]
        desc = _changes_html(
            "Atualização da branch test",
            ["Branch: " + _anchor(SITE + "/commits/test", "test"),
             "Commit instalado: " + html.escape((before or "desconhecido")[:12]),
             "Commit disponível: " + _anchor(SITE + "/commit/" + current, current[:12])],
            changes)
        return {"source": "test", "commit": current,
                "title": "Savage Wind — Atualização test",
                "html": desc, "revision": current}

    # A selection digest alone cannot identify which pull requests were used.
    selected = state.get("custom_prs")
    if not selected:
        return None
    selected = validated(selected)
    updated, changes = [], []
    base_before = state.get("custom_base")
    base_changed = base_before != current
    if base_changed:
        changes.extend(_commit_changes(base_before, current))
        if not changes:
            changes.append("A branch " + _anchor(SITE + "/commits/test", "test")
                           + " recebeu alterações.")
    # Independent PR lookups run concurrently so startup latency does not
    # grow linearly with the number of selected pull requests.
    with ThreadPoolExecutor(max_workers=min(4, len(selected))) as pool:
        records = list(pool.map(lambda pr: _github_json(
            "/pulls/" + str(pr["number"])), selected))
    compared = 0
    for entry, info in zip(selected, records):
        number = entry["number"]
        head = (info.get("head") or {}).get("sha")
        base = (info.get("base") or {}).get("ref")
        if not isinstance(head, str) or not SHA.fullmatch(head) or base != "test":
            raise InstallError(f"Cannot verify pull request #{number} against upstream test.")
        updated.append({"number": number, "sha": head})
        if head != entry["sha"]:
            label = f"PR #{number}: {(info.get('title') or 'Alterações')[:120]}"
            changes.append(_anchor(SITE + "/pull/" + str(number), label)
                           + " (" + html.escape(entry["sha"][:8])
                           + " → " + html.escape(head[:8]) + ")")
            if compared < 2:
                changes.extend(_commit_changes(entry["sha"], head)[:2])
                compared += 1
    if not base_changed and updated == selected:
        return None
    desc = _changes_html(
        "Atualização da compilação com PRs",
        ["Base: " + _anchor(SITE + "/commits/test", "upstream test"),
         "PRs monitorados: " + ", ".join(_anchor(SITE + "/pull/" + str(pr["number"]),
                                                  "#" + str(pr["number"])) for pr in updated)],
        changes)
    return {"source": "custom", "prs": updated, "base_commit": current,
            "title": "Savage Wind — Atualização dos PRs",
            "html": desc,
            "revision": current + ":" + ":".join(p["sha"] for p in updated)}


def _preference_path(root):
    path = Path(root) / ".sw-wrapper" / PREFERENCES
    if path.is_symlink():
        raise InstallError("Update preferences cannot be a symbolic link.")
    return path


def suppressed(root, source):
    path = _preference_path(root)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get(source) is True if isinstance(data, dict) else False
    except (OSError, ValueError):
        return False


def suppress(root, source):
    if source not in ("official", "test", "custom"):
        raise InstallError("Cannot save an unknown update preference.")
    path = _preference_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open(encoding="utf-8") as stream:
            prefs = json.load(stream)
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}
    prefs[source] = True
    fd, temp = tempfile.mkstemp(prefix=".update-prefs-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(prefs, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


# The upstream titles and commit messages remain untouched. Only our fixed
# headings and labels are translated; all GitHub links stay clickable.
_LABELS = {
    "Savage Wind — Atualização oficial": ("Savage Wind — Official update", "Savage Wind — Официальное обновление", "Savage Wind — 官方更新"),
    "Savage Wind — Atualização test": ("Savage Wind — Test update", "Savage Wind — Обновление test", "Savage Wind — test 更新"),
    "Savage Wind — Atualização dos PRs": ("Savage Wind — PR updates", "Savage Wind — Обновление PR", "Savage Wind — PR 更新"),
    "Atualização oficial disponível": ("Official update available", "Доступно официальное обновление", "有可用的官方更新"),
    "Atualização da branch test": ("Upstream test branch update", "Обновление ветки test", "上游 test 分支更新"),
    "Atualização da compilação com PRs": ("PR build update", "Обновление сборки с PR", "PR 构建更新"),
    "Versão instalada:": ("Installed version:", "Установленная версия:", "已安装版本："),
    "Commit instalado:": ("Installed commit:", "Установленный коммит:", "已安装提交："),
    "Commit disponível:": ("Available commit:", "Доступный коммит:", "可用提交："),
    "PRs monitorados:": ("Tracked PRs:", "Отслеживаемые PR:", "已跟踪的 PR："),
    "Nova release oficial publicada; detalhes no link da release.": (
        "A new official release was published; see the linked release.",
        "Опубликован новый официальный выпуск; подробности по ссылке.",
        "已发布新的官方版本；请查看链接中的发行说明。"),
    "Alterações detectadas; consulte o histórico da branch.": (
        "Changes detected; see the branch history.",
        "Обнаружены изменения; смотрите историю ветки.",
        "检测到更改；请查看分支历史。"),
    " recebeu alterações.": (" has new commits.", " получила новые коммиты.", " 有新的提交。"),
}
_LANG = {"en": 0, "ru": 1, "zh-Hans": 2}


def localized(info, language):
    """Translate our changelog labels while retaining GitHub links and commit text."""
    if language not in _LANG:
        return info
    index = _LANG[language]
    result = dict(info)
    for original, values in _LABELS.items():
        result["title"] = result["title"].replace(original, values[index])
        result["html"] = result["html"].replace(original, html.escape(values[index]))
    return result
