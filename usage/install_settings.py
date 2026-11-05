"""Merge the statusline, PreCompact hook and settings keys into
`~/.claude/settings.json` (spec Sec 4.9).

`merge_settings` is a pure, idempotent dict transform. `main` is the CLI
wrapper invoked as `<python> -m usage.install_settings <settings.json>
<repo_root> <python>`, where install.sh resolves `<python>` to an absolute
Python 3.15 interpreter. It backs up an existing file before writing the
merged result, creates the file (and parent directories) if it does not
exist yet, and refuses to touch anything if the existing file is not valid
JSON.
"""
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Earlier versions of this installer wrote autoCompactWindow=200000, which
# made Claude Code compact on its own at 200K. This tool only alerts, it never
# decides when to compact, so that exact value is removed on the next install.
# Any other value was chosen by the user and is left alone.
LEGACY_AUTO_COMPACT_WINDOW = 200000
CLEANUP_DAYS = 90
HOOK_NAME = "precompact-notify.sh"

_CHANGED_KEYS = ("statusLine", "hooks.PreCompact", "cleanupPeriodDays")


def _statusline_entry(repo: str, python: str) -> dict[str, Any]:
    # Claude Code runs these through a shell: quote both paths so spaces or
    # shell characters in either location stay part of the path. `python` is
    # an absolute interpreter path, never a bare `python3`: on macOS that
    # finds the system 3.9, which cannot run this code.
    command = shlex.quote(python) + " " + shlex.quote(repo + "/statusline/statusline.py")
    return {"type": "command", "command": command, "padding": 0}


def _precompact_entry(repo: str) -> dict[str, Any]:
    return {
        "matcher": "auto",
        "hooks": [{"type": "command", "command": shlex.quote("%s/hooks/%s" % (repo, HOOK_NAME)), "timeout": 10}],
    }


def _is_our_precompact_entry(entry: Any) -> bool:
    """True if `entry` is a prior PreCompact registration of our own hook script,
    identified by any of its hook commands ending in HOOK_NAME (any repo path,
    quoted or not)."""
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks") or []:
        command = hook.get("command") if isinstance(hook, dict) else None
        if isinstance(command, str) and command.rstrip("'").endswith(HOOK_NAME):
            return True
    return False


def _coerce_cleanup_days(value: Any) -> int:
    """Tolerant coercion for an existing `cleanupPeriodDays`: int/float -> int,
    a numeric string -> int, bool/None/anything else unusable -> 0 (so it never
    lowers the 90-day floor, but also never raises on a malformed value)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def merge_settings(data: dict[str, Any], repo: str, python: str) -> dict[str, Any]:
    """Return a new settings dict: `data` plus the statusline command run by
    the `python` interpreter, the
    PreCompact hook and `cleanupPeriodDays`, minus the `autoCompactWindow`
    an earlier installer wrote. Does not mutate `data`. Idempotent: merging
    the result again is a no-op."""
    out = dict(data)

    out["statusLine"] = _statusline_entry(repo, python)

    hooks = dict(out.get("hooks") or {})
    existing_pre = hooks.get("PreCompact") or []
    kept = [entry for entry in existing_pre if not _is_our_precompact_entry(entry)]
    hooks["PreCompact"] = kept + [_precompact_entry(repo)]
    out["hooks"] = hooks

    if out.get("autoCompactWindow") == LEGACY_AUTO_COMPACT_WINDOW:
        del out["autoCompactWindow"]

    out["cleanupPeriodDays"] = max(_coerce_cleanup_days(out.get("cleanupPeriodDays")), CLEANUP_DAYS)

    return out


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: <python> -m usage.install_settings <settings.json> <repo_root> <python>", file=sys.stderr)
        return 2

    path = Path(argv[0]).expanduser()
    repo, python = argv[1], argv[2]
    if not Path(python).is_absolute():
        # A bare name would be looked up on Claude Code's PATH on every prompt.
        print("error: <python> must be an absolute path, got %r" % python, file=sys.stderr)
        return 2

    data: dict[str, Any] = {}
    backup_path = None
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError as exc:
            print("error: %s is not valid JSON: %s" % (path, exc), file=sys.stderr)
            return 1
        backup_path = _backup_path(path)
        shutil.copy2(str(path), str(backup_path))

    merged = merge_settings(data, repo, python)
    _atomic_write_json(path, merged)

    summary = "set %s" % ", ".join(_CHANGED_KEYS)
    if backup_path is not None:
        print("Updated %s (%s); backup saved to %s" % (path, summary, backup_path))
    else:
        print("Created %s (%s); no prior file to back up" % (path, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
