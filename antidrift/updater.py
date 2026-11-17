"""GitHub update checker and installer for Anti-Drift Studio."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

GITHUB_OWNER = "cesarmoralesb20005-ctrl"
GITHUB_REPO = "Anti-Drift-Studio"
GITHUB_BRANCH = "main"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAMP_PATH = (
    Path.home() / ".config" / "antidrift-studio" / "installed_commit.txt"
)

_HTTP_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Anti-Drift-Studio-Updater/1.0",
}


@dataclass(frozen=True)
class GitHubTarget:
    """Resolved GitHub repository owner, name, and tracking branch."""

    owner: str = GITHUB_OWNER
    repo: str = GITHUB_REPO
    branch: str = GITHUB_BRANCH

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def api_commit_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.repo}"
            f"/commits/{self.branch}"
        )

    def api_compare_url(self, base_sha: str) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.repo}"
            f"/compare/{base_sha}...{self.branch}"
        )

    @property
    def api_latest_release_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.owner}/{self.repo}"
            "/releases/latest"
        )

    @property
    def archive_tarball_url(self) -> str:
        return (
            f"https://github.com/{self.owner}/{self.repo}"
            f"/archive/refs/heads/{self.branch}.tar.gz"
        )


@dataclass
class UpdateCheckResult:
    """Outcome of checking GitHub for newer Anti-Drift Studio commits/releases."""

    update_available: bool
    local_version: str
    local_sha: str = ""
    remote_sha: str = ""
    remote_tag: str | None = None
    remote_message: str = ""
    remote_date: str = ""
    ahead_by: int = 0
    commits: list[str] = field(default_factory=list)
    repo_url: str = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
    branch: str = GITHUB_BRANCH
    source: str = "github_api"
    error: str | None = None

    @property
    def local_short_sha(self) -> str:
        return self.local_sha[:7] if self.local_sha else "—"

    @property
    def remote_short_sha(self) -> str:
        return self.remote_sha[:7] if self.remote_sha else "—"


def parse_github_remote_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from an HTTPS or SSH GitHub remote URL."""
    text = url.strip()
    m = re.match(
        r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?$",
        text,
    )
    if not m:
        return None
    return m.group(1), m.group(2)


def resolve_github_target(project_root: Path = PROJECT_ROOT) -> GitHubTarget:
    """Detect GitHub owner/repo/branch from local git remote if present."""
    owner, repo, branch = GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH
    if (project_root / ".git").exists() and shutil.which("git"):
        try:
            res_url = subprocess.run(
                ["git", "-C", str(project_root), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res_url.returncode == 0:
                parsed = parse_github_remote_url(res_url.stdout)
                if parsed is not None:
                    owner, repo = parsed
            res_br = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_root),
                    "rev-parse",
                    "--abbrev-ref",
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            br = res_br.stdout.strip()
            if res_br.returncode == 0 and br and br != "HEAD":
                branch = br
        except Exception:
            pass
    return GitHubTarget(owner=owner, repo=repo, branch=branch)


def read_local_app_version(project_root: Path = PROJECT_ROOT) -> str:
    """Read local package version string from pyproject.toml."""
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    return "1.0.0"


def get_local_commit_sha(
    project_root: Path = PROJECT_ROOT,
    stamp_path: Path = DEFAULT_STAMP_PATH,
) -> str:
    """Return local git HEAD SHA, or stamped commit SHA for archive installs."""
    if (project_root / ".git").exists() and shutil.which("git"):
        try:
            res = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                sha = res.stdout.strip()
                if re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
                    return sha
        except Exception:
            pass
    if stamp_path.exists():
        try:
            sha = stamp_path.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
                return sha
        except Exception:
            pass
    return ""


def _parse_version_tuple(raw: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", raw)]
    return tuple(nums) if nums else (0,)


def is_newer_version(remote_tag: str, local_version: str) -> bool:
    """Compare semantic version strings such as 'v1.2.0' > '1.0.0'."""
    return _parse_version_tuple(remote_tag) > _parse_version_tuple(local_version)


def _fetch_json(url: str, timeout: float = 6.0) -> dict | list:
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _local_has_ancestor(project_root: Path, sha: str) -> bool:
    """Check if local HEAD already contains the given remote commit SHA."""
    if not sha or not (project_root / ".git").exists() or not shutil.which("git"):
        return False
    try:
        res = subprocess.run(
            ["git", "-C", str(project_root), "merge-base", "--is-ancestor", sha, "HEAD"],
            capture_output=True,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


def check_for_updates(
    project_root: Path = PROJECT_ROOT,
    timeout: float = 6.0,
    stamp_path: Path = DEFAULT_STAMP_PATH,
    fetcher: Callable[[str, float], dict | list] = _fetch_json,
) -> UpdateCheckResult:
    """Query GitHub API (with git fallback) for newer updates on the tracking branch."""
    target = resolve_github_target(project_root)
    local_ver = read_local_app_version(project_root)
    local_sha = get_local_commit_sha(project_root, stamp_path=stamp_path)

    remote_tag: str | None = None
    try:
        commit_data = fetcher(target.api_commit_url, timeout)
        if not isinstance(commit_data, dict):
            raise ValueError("Respuesta inesperada de GitHub API")

        try:
            rel_data = fetcher(target.api_latest_release_url, min(timeout, 3.0))
            if isinstance(rel_data, dict):
                tag = str(rel_data.get("tag_name") or "").strip()
                if tag:
                    remote_tag = tag
        except Exception:
            pass

        remote_sha = str(commit_data.get("sha") or "").strip()
        c_obj = commit_data.get("commit") or {}
        raw_msg = str(c_obj.get("message") or "").strip()
        remote_msg = raw_msg.splitlines()[0] if raw_msg else ""
        committer = c_obj.get("committer") or c_obj.get("author") or {}
        remote_date = str(committer.get("date") or "")[:10]

        update_available = False
        ahead_by = 0
        commits: list[str] = []

        if local_sha and remote_sha:
            if local_sha.lower() == remote_sha.lower():
                update_available = False
            elif _local_has_ancestor(project_root, remote_sha):
                update_available = False
            else:
                try:
                    cmp_data = fetcher(target.api_compare_url(local_sha), timeout)
                    if isinstance(cmp_data, dict):
                        status = str(cmp_data.get("status") or "")
                        raw_ahead = int(cmp_data.get("ahead_by") or 0)
                        if status == "identical":
                            update_available = False
                        elif status == "behind":
                            # In compare/{local}...{remote}, 'behind' means remote lacks local commits.
                            update_available = False
                        elif status in ("ahead", "diverged"):
                            ahead_by = raw_ahead
                            update_available = raw_ahead > 0
                            for item in (cmp_data.get("commits") or [])[-6:]:
                                sha7 = str(item.get("sha") or "")[:7]
                                msg_full = str(
                                    (item.get("commit") or {}).get("message") or ""
                                )
                                subj = msg_full.splitlines()[0] if msg_full else ""
                                if sha7 and subj:
                                    commits.append(f"{sha7} · {subj}")
                            commits.reverse()
                except Exception:
                    update_available = True
                    ahead_by = 1
                    if remote_sha and remote_msg:
                        commits = [f"{remote_sha[:7]} · {remote_msg}"]
        elif remote_tag:
            update_available = is_newer_version(remote_tag, local_ver)
            ahead_by = 1 if update_available else 0
        elif remote_sha:
            # Archive install with no previous commit stamp recorded yet
            update_available = True
            ahead_by = 1
            if remote_msg:
                commits = [f"{remote_sha[:7]} · {remote_msg}"]

        return UpdateCheckResult(
            update_available=update_available,
            local_version=local_ver,
            local_sha=local_sha,
            remote_sha=remote_sha,
            remote_tag=remote_tag,
            remote_message=remote_msg,
            remote_date=remote_date,
            ahead_by=ahead_by,
            commits=commits,
            repo_url=target.repo_url,
            branch=target.branch,
            source="github_api",
        )
    except Exception as api_exc:
        # Fallback: git fetch origin <branch> if running from a git clone
        if (project_root / ".git").exists() and shutil.which("git"):
            try:
                subprocess.run(
                    ["git", "-C", str(project_root), "fetch", "--quiet", "origin", target.branch],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(8, int(timeout) + 4),
                )
                r_sha = subprocess.check_output(
                    ["git", "-C", str(project_root), "rev-parse", f"origin/{target.branch}"],
                    text=True,
                    timeout=3,
                ).strip()
                log_raw = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(project_root),
                        "log",
                        "-n",
                        "6",
                        "--pretty=format:%h · %s",
                        f"HEAD..origin/{target.branch}",
                    ],
                    text=True,
                    timeout=3,
                ).strip()
                commits = [line for line in log_raw.splitlines() if line.strip()]
                ahead_by = int(
                    subprocess.check_output(
                        [
                            "git",
                            "-C",
                            str(project_root),
                            "rev-list",
                            "--count",
                            f"HEAD..origin/{target.branch}",
                        ],
                        text=True,
                        timeout=3,
                    ).strip()
                    or "0"
                )
                msg = commits[0].split(" · ", 1)[-1] if commits else ""
                r_date = subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(project_root),
                        "log",
                        "-1",
                        "--pretty=format:%cs",
                        f"origin/{target.branch}",
                    ],
                    text=True,
                    timeout=3,
                ).strip()
                return UpdateCheckResult(
                    update_available=ahead_by > 0,
                    local_version=local_ver,
                    local_sha=local_sha,
                    remote_sha=r_sha,
                    remote_tag=remote_tag,
                    remote_message=msg,
                    remote_date=r_date,
                    ahead_by=ahead_by,
                    commits=commits,
                    repo_url=target.repo_url,
                    branch=target.branch,
                    source="git",
                )
            except Exception:
                pass

        return UpdateCheckResult(
            update_available=False,
            local_version=local_ver,
            local_sha=local_sha,
            remote_tag=remote_tag,
            repo_url=target.repo_url,
            branch=target.branch,
            source="offline",
            error=f"No se pudo conectar con GitHub ({api_exc})",
        )


def _safe_extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    """Safely extract GitHub archive tarball and return the inner root folder."""
    with tarfile.open(archive_path, "r:gz") as tf:
        for member in tf.getmembers():
            target_path = (dest_dir / member.name).resolve()
            if not str(target_path).startswith(str(dest_dir.resolve())):
                raise ValueError(f"Ruta insegura en archivo descargado: {member.name}")
        tf.extractall(dest_dir)
    roots = [p for p in dest_dir.iterdir() if p.is_dir()]
    if not roots:
        raise RuntimeError("El archivo descargado de GitHub está vacío.")
    return roots[0]


def _sync_native_assets_if_installed(
    project_root: Path, progress_cb: Callable[[str], None]
) -> None:
    """Recompile daemon and refresh helper scripts if already installed in ~/.local/bin."""
    bin_dir = Path.home() / ".local" / "bin"
    daemon_bin = bin_dir / "xbox-antidrift"
    daemon_src = project_root / "daemon" / "xbox-antidrift.c"
    if daemon_bin.exists() and daemon_src.exists() and shutil.which("gcc"):
        progress_cb("Recompilando servicio nativo anti-drift...")
        res = subprocess.run(
            ["gcc", "-O2", "-Wall", "-Wextra", str(daemon_src), "-o", str(daemon_bin), "-lm"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode == 0:
            shutil.copy2(daemon_src, bin_dir / "xbox-antidrift.c")
            if shutil.which("systemctl"):
                subprocess.run(
                    ["systemctl", "--user", "restart", "xbox-antidrift.service"],
                    capture_output=True,
                    timeout=10,
                )

    clip_script_src = project_root / "scripts" / "antidrift-save-clip"
    clip_script_dst = bin_dir / "antidrift-save-clip"
    if clip_script_src.exists() and bin_dir.exists():
        shutil.copy2(clip_script_src, clip_script_dst)
        mode = clip_script_dst.stat().st_mode
        clip_script_dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def apply_update(
    project_root: Path = PROJECT_ROOT,
    progress_cb: Callable[[str], None] | None = None,
    remote_sha: str = "",
    stamp_path: Path = DEFAULT_STAMP_PATH,
) -> tuple[bool, str, bool]:
    """Download and apply the latest update from GitHub.

    Returns (success, status_message, restart_recommended).
    """
    cb = progress_cb or (lambda _msg: None)
    target = resolve_github_target(project_root)

    if (project_root / ".git").exists() and shutil.which("git"):
        try:
            cb("Verificando estado del repositorio local...")
            dirty = subprocess.check_output(
                ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=no"],
                text=True,
                timeout=5,
            ).strip()
            if dirty:
                return (
                    False,
                    "Hay archivos modificados sin commit en la carpeta local. "
                    "Guarda o descarta tus cambios antes de actualizar.",
                    False,
                )

            cb(f"Descargando cambios desde origin/{target.branch}...")
            fetch_res = subprocess.run(
                ["git", "-C", str(project_root), "fetch", "origin", target.branch],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if fetch_res.returncode != 0:
                return (
                    False,
                    f"Error al consultar GitHub: {fetch_res.stderr.strip() or 'git fetch falló'}",
                    False,
                )

            cb("Aplicando actualización (git pull --ff-only)...")
            pull_res = subprocess.run(
                ["git", "-C", str(project_root), "pull", "--ff-only", "origin", target.branch],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if pull_res.returncode != 0:
                return (
                    False,
                    f"No se pudo hacer fast-forward automático: {pull_res.stderr.strip()}",
                    False,
                )

            new_sha = get_local_commit_sha(project_root, stamp_path=stamp_path)
            _sync_native_assets_if_installed(project_root, cb)
            return (
                True,
                f"Actualización aplicada correctamente ({new_sha[:7] or target.branch}).",
                True,
            )
        except Exception as exc:
            return False, f"Error actualizando mediante Git: {exc}", False

    # Archive-based update fallback when installed without .git metadata
    try:
        cb("Descargando paquete actualizado desde GitHub...")
        with tempfile.TemporaryDirectory(prefix="antidrift-update-") as tmp:
            tmp_dir = Path(tmp)
            tar_path = tmp_dir / "repo.tar.gz"
            req = urllib.request.Request(target.archive_tarball_url, headers=_HTTP_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp, tar_path.open("wb") as out:
                shutil.copyfileobj(resp, out)

            cb("Extrayendo archivos...")
            extracted_root = _safe_extract_archive(tar_path, tmp_dir)

            cb("Copiando archivos nuevos...")
            preserved = {".git", ".venv", "__pycache__"}
            for item in extracted_root.iterdir():
                if item.name in preserved:
                    continue
                dest = project_root / item.name
                if item.is_dir():
                    shutil.copytree(
                        item,
                        dest,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"),
                    )
                else:
                    shutil.copy2(item, dest)

        if remote_sha:
            stamp_path.parent.mkdir(parents=True, exist_ok=True)
            stamp_path.write_text(remote_sha + "\n", encoding="utf-8")

        _sync_native_assets_if_installed(project_root, cb)
        return (
            True,
            f"Actualización aplicada correctamente ({remote_sha[:7] or target.branch}).",
            True,
        )
    except Exception as exc:
        return False, f"No se pudo descargar la actualización desde GitHub: {exc}", False


def restart_application(project_root: Path = PROJECT_ROOT) -> bool:
    """Spawn a fresh Anti-Drift Studio instance detached from the current process."""
    run_sh = project_root / "run.sh"
    app_py = project_root / "app.py"
    env = os.environ.copy()
    try:
        if run_sh.exists() and os.access(run_sh, os.X_OK):
            cmd = [str(run_sh)]
        else:
            cmd = [sys.executable, str(app_py)]
        subprocess.Popen(
            cmd,
            cwd=str(project_root),
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False
