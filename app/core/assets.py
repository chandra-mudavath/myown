"""Static file locations, and the asset() template helper that adds a cache-busting version.

Each module serves its own static/ folder. asset("admin", "css/admin.css") returns
"/static/admin/css/admin.css?v=<hash of the file>", so the URL changes whenever the file
does and nobody has to bump ?v= by hand.
"""
import hashlib
from pathlib import Path

# (module, url prefix, directory). Order matters when mounting: the shared /static mount
# must come last or it would swallow the module paths.
STATIC_MOUNTS = [
    ("public", "/static/public", "app/public/static"),
    ("client", "/static/client", "app/modules/client/static"),
    ("staff", "/static/staff", "app/modules/staff/static"),
    ("admin", "/static/admin", "app/modules/admin/static"),
    ("hr", "/static/hr", "app/modules/hr/static"),
    ("platform", "/static", "app/platform/static"),
]
_LOCATIONS = {module: (prefix, Path(directory)) for module, prefix, directory in STATIC_MOUNTS}
_versions: dict[Path, tuple[float, str]] = {}


def mount_name(module: str) -> str:
    """Route name of a module's static mount, for url_for ("static" for the shared one)."""
    return "static" if module == "platform" else f"{module}_static"


def _version(file: Path) -> str:
    mtime = file.stat().st_mtime
    cached = _versions.get(file)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.md5(file.read_bytes()).hexdigest()[:8]
    _versions[file] = (mtime, digest)
    return digest


def asset(module: str, path: str) -> str:
    """URL of a static file in a module's static/ folder, with a content-hash version."""
    prefix, directory = _LOCATIONS[module]
    path = path.lstrip("/")
    return f"{prefix}/{path}?v={_version(directory / path)}"
