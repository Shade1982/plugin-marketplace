"""
Shared helpers for the Gameyfin plugin marketplace scripts.

Only the Python standard library is used, so the scripts run on any GitHub Actions runner
(and locally) without installing dependencies. Requires Python 3.10+.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR_NAME = "plugins"
INDEX_FILE_NAME = "plugins.json"

# Limits enforced by Gameyfin (see MarketplaceHttpClient)
MAX_INDEX_SIZE = 5 * 1024 * 1024
MAX_LOGO_SIZE = 1 * 1024 * 1024
MAX_PLUGIN_SIZE = 512 * 1024 * 1024

DOWNLOAD_TIMEOUT_SECONDS = 120
USER_AGENT = "gameyfin-plugin-marketplace-ci (+https://github.com/gameyfin/plugin-marketplace)"

# Plugin IDs starting with this prefix are reserved for plugins published by the Gameyfin team
RESERVED_ID_PREFIX = "org.gameyfin."

# Extension points of the Gameyfin plugin API (simple class names, as reported by Gameyfin)
KNOWN_PLUGIN_TYPES = {"GameMetadataProvider", "DownloadProvider", "GameyfinTask"}

PLUGIN_ID_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?$")
SHA512_REGEX = re.compile(r"^[0-9a-f]{128}$")
# Lenient check for PF4J (jsemver) version constraints, e.g. ">=2.5.1", ">=2.5.1 & <3.0.0" or "*"
REQUIRES_REGEX = re.compile(r"^(\*|[0-9A-Za-z.*~^<>=!&|()\s-]+)$")
GITHUB_LOGIN_REGEX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
TAG_REGEX = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")

PLUGIN_FIELDS_REQUIRED = {"id", "name", "description", "provider", "license", "projectUrl", "types", "maintainers",
                          "releases"}
PLUGIN_FIELDS_OPTIONAL = {"$schema", "shortDescription", "logo", "tags"}
RELEASE_FIELDS_REQUIRED = {"version", "date", "requires", "url", "sha512sum"}
RELEASE_FIELDS_OPTIONAL = {"changelog"}

# Fields published in plugins.json (everything else, e.g. "maintainers", is only used by this repository)
INDEX_PLUGIN_FIELDS = ["id", "name", "shortDescription", "description", "provider", "license", "projectUrl", "logo",
                       "types", "tags", "releases"]
INDEX_RELEASE_FIELDS = ["version", "date", "requires", "url", "sha512sum", "changelog"]

LOGO_SIGNATURES = {
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/gif": lambda data: data.startswith(b"GIF87a") or data.startswith(b"GIF89a"),
    "image/webp": lambda data: data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    "image/svg+xml": lambda data: b"<svg" in data[:4096].lower(),
}


@dataclass
class Report:
    """Collects errors and warnings and prints them (as GitHub annotations when running in GitHub Actions)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def error(self, message: str, file: str | None = None) -> None:
        self.errors.append(_with_file(message, file))
        _annotate("error", message, file)

    def warning(self, message: str, file: str | None = None) -> None:
        self.warnings.append(_with_file(message, file))
        _annotate("warning", message, file)

    def notice(self, message: str, file: str | None = None) -> None:
        self.notices.append(_with_file(message, file))
        _annotate("notice", message, file)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_markdown(self, title: str) -> str:
        lines = [f"## {title}", ""]
        if self.ok:
            lines.append(":white_check_mark: All checks passed.")
        else:
            lines.append(f":x: {len(self.errors)} error(s)")
        for heading, entries in (("Errors", self.errors), ("Warnings", self.warnings), ("Notes", self.notices)):
            if entries:
                lines += ["", f"### {heading}", ""]
                lines += [f"- {entry}" for entry in entries]
        return "\n".join(lines) + "\n"


def _with_file(message: str, file: str | None) -> str:
    return f"`{file}`: {message}" if file else message


def _annotate(level: str, message: str, file: str | None) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        location = f" file={file}" if file else ""
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level}{location}::{escaped}")
    else:
        prefix = {"error": "ERROR", "warning": "WARNING", "notice": "NOTE"}[level]
        print(f"{prefix}: {_with_file(message, file)}")


# ---------------------------------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------------------------------

def version_key(version: str) -> tuple:
    """Sort key for semantic versions (pre-releases sort before the corresponding release)."""
    match = SEMVER_REGEX.match(version)
    if not match:
        return (-1, -1, -1, 0, ())
    major, minor, patch, pre = match.groups()
    pre_key: tuple = ()
    if pre:
        pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    # Releases without pre-release identifiers have a higher precedence
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre_key)


# ---------------------------------------------------------------------------------------------------------------------
# Manifest loading & structural validation
# ---------------------------------------------------------------------------------------------------------------------

def plugins_dir(repo: Path) -> Path:
    return repo / PLUGINS_DIR_NAME


def manifest_paths(repo: Path) -> list[Path]:
    return sorted(plugins_dir(repo).glob("*.json"))


def relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_json(path: Path, report: Report, display_name: str) -> Any | None:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as e:
        report.error(f"Invalid JSON: {e}", display_name)
    except OSError as e:
        report.error(f"Cannot read file: {e}", display_name)
    return None


def is_https_url(value: Any, allow_insecure: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    schemes = ("https://", "http://") if allow_insecure else ("https://",)
    if not value.startswith(schemes):
        return False
    rest = value.split("://", 1)[1]
    host = rest.split("/", 1)[0]
    return bool(host) and "@" not in host and not any(c.isspace() for c in value)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_manifest(manifest: Any, file_name: str, report: Report, allow_insecure: bool = False) -> bool:
    """Validates the structure of a plugin manifest (no network access). Returns True if it is valid."""
    errors_before = len(report.errors)

    if not isinstance(manifest, dict):
        report.error("The manifest must be a JSON object", file_name)
        return False

    missing = sorted(PLUGIN_FIELDS_REQUIRED - manifest.keys())
    if missing:
        report.error(f"Missing required field(s): {', '.join(missing)}", file_name)
    unknown = sorted(manifest.keys() - PLUGIN_FIELDS_REQUIRED - PLUGIN_FIELDS_OPTIONAL)
    if unknown:
        report.error(f"Unknown field(s): {', '.join(unknown)}", file_name)

    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID_REGEX.match(plugin_id):
        report.error("'id' must match " + PLUGIN_ID_REGEX.pattern, file_name)
    else:
        expected_name = f"{plugin_id}.json"
        if Path(file_name).name != expected_name:
            report.error(f"The manifest file must be named '{expected_name}'", file_name)
        if plugin_id.startswith(RESERVED_ID_PREFIX):
            report.warning(
                f"Plugin IDs starting with '{RESERVED_ID_PREFIX}' are reserved for official Gameyfin plugins",
                file_name)

    for key, max_length in (("name", 100), ("provider", 100), ("license", 100), ("shortDescription", 300),
                            ("description", 20_000)):
        if key not in manifest:
            continue  # missing required fields are reported above
        value = manifest.get(key)
        if not _non_empty_string(value):
            report.error(f"'{key}' must be a non-empty string", file_name)
        elif len(value) > max_length:
            report.error(f"'{key}' must not be longer than {max_length} characters", file_name)

    if "projectUrl" in manifest and not is_https_url(manifest.get("projectUrl"), allow_insecure):
        report.error("'projectUrl' must be an absolute https:// URL", file_name)
    if "logo" in manifest and not is_https_url(manifest.get("logo"), allow_insecure):
        report.error("'logo' must be an absolute https:// URL", file_name)

    types = manifest.get("types")
    if "types" in manifest:
        if not isinstance(types, list) or not types or not all(isinstance(t, str) for t in types):
            report.error("'types' must be a non-empty list of strings", file_name)
        else:
            unknown_types = sorted(set(types) - KNOWN_PLUGIN_TYPES)
            if unknown_types:
                report.error(f"Unknown plugin type(s): {', '.join(unknown_types)} "
                             f"(known: {', '.join(sorted(KNOWN_PLUGIN_TYPES))})", file_name)
            if len(set(types)) != len(types):
                report.error("'types' must not contain duplicates", file_name)

    if "tags" in manifest:
        tags = manifest.get("tags")
        if not isinstance(tags, list) or len(tags) > 10 or not all(
                isinstance(t, str) and TAG_REGEX.match(t) for t in tags):
            report.error("'tags' must be a list of at most 10 lowercase tags matching " + TAG_REGEX.pattern,
                         file_name)

    if "maintainers" in manifest:
        maintainers = manifest.get("maintainers")
        if not isinstance(maintainers, list) or not maintainers or not all(
                isinstance(m, str) and GITHUB_LOGIN_REGEX.match(m) for m in maintainers):
            report.error("'maintainers' must be a non-empty list of GitHub usernames", file_name)

    if "releases" in manifest:
        releases = manifest.get("releases")
        if not isinstance(releases, list) or not releases:
            report.error("'releases' must be a non-empty list", file_name)
        else:
            seen_versions: set[str] = set()
            for index, release in enumerate(releases):
                _validate_release(release, f"releases[{index}]", seen_versions, file_name, report, allow_insecure)

    return len(report.errors) == errors_before


def _validate_release(release: Any, path: str, seen_versions: set[str], file_name: str, report: Report,
                      allow_insecure: bool) -> None:
    if not isinstance(release, dict):
        report.error(f"{path} must be an object", file_name)
        return

    missing = sorted(RELEASE_FIELDS_REQUIRED - release.keys())
    if missing:
        report.error(f"{path}: missing required field(s): {', '.join(missing)}", file_name)
    unknown = sorted(release.keys() - RELEASE_FIELDS_REQUIRED - RELEASE_FIELDS_OPTIONAL)
    if unknown:
        report.error(f"{path}: unknown field(s): {', '.join(unknown)}", file_name)

    version = release.get("version")
    if not isinstance(version, str) or not SEMVER_REGEX.match(version):
        report.error(f"{path}: 'version' must be a semantic version (e.g. 1.2.3)", file_name)
    elif version in seen_versions:
        report.error(f"{path}: duplicate version '{version}'", file_name)
    else:
        seen_versions.add(version)

    date = release.get("date")
    if not isinstance(date, str) or not DATE_REGEX.match(date):
        report.error(f"{path}: 'date' must be an ISO-8601 date (yyyy-MM-dd) or date-time", file_name)

    requires = release.get("requires")
    if not isinstance(requires, str) or not requires.strip() or not REQUIRES_REGEX.match(requires):
        report.error(f"{path}: 'requires' must be a version constraint like '>=2.5.1' (or '*')", file_name)

    if not is_https_url(release.get("url"), allow_insecure):
        report.error(f"{path}: 'url' must be an absolute https:// URL", file_name)

    sha512sum = release.get("sha512sum")
    if not isinstance(sha512sum, str) or not SHA512_REGEX.match(sha512sum):
        report.error(f"{path}: 'sha512sum' must be the SHA-512 checksum of the plugin file "
                     f"(128 lowercase hex characters)", file_name)

    if "changelog" in release and not isinstance(release.get("changelog"), str):
        report.error(f"{path}: 'changelog' must be a string", file_name)


# ---------------------------------------------------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------------------------------------------------

def to_index_entry(manifest: dict) -> dict:
    entry = {key: manifest[key] for key in INDEX_PLUGIN_FIELDS if key in manifest and key != "releases"}
    releases = sorted(manifest["releases"], key=lambda r: version_key(r["version"]), reverse=True)
    entry["releases"] = [{key: r[key] for key in INDEX_RELEASE_FIELDS if key in r} for r in releases]
    return entry


def build_index(manifests: list[dict]) -> dict:
    return {"plugins": [to_index_entry(m) for m in sorted(manifests, key=lambda m: m["id"])]}


def dump_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------------------------------------------------

class DownloadError(Exception):
    pass


def download(url: str, max_bytes: int, target: Path, allow_insecure: bool = False) -> str:
    """Downloads url to target (enforcing max_bytes). Returns the content type reported by the server."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            if not is_https_url(final_url, allow_insecure):
                raise DownloadError(f"Redirected to a non-https URL: {final_url}")
            length = response.headers.get("Content-Length")
            if length is not None and length.isdigit() and int(length) > max_bytes:
                raise DownloadError(f"File is too large ({int(length)} bytes, max. {max_bytes} bytes)")
            read = 0
            with target.open("wb") as file:
                while chunk := response.read(64 * 1024):
                    read += len(chunk)
                    if read > max_bytes:
                        raise DownloadError(f"File is too large (more than {max_bytes} bytes)")
                    file.write(chunk)
            return response.headers.get_content_type()
    except urllib.error.HTTPError as e:
        raise DownloadError(f"HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise DownloadError(str(getattr(e, "reason", e))) from e


def sha512_of(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------------------------------------------------
# Plugin JAR inspection
# ---------------------------------------------------------------------------------------------------------------------

def parse_jar_manifest(text: str) -> dict[str, str]:
    """Parses the main section of a JAR manifest (handles continuation lines)."""
    attributes: dict[str, str] = {}
    last_key: str | None = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line == "":
            break  # end of the main section
        if line.startswith(" ") and last_key is not None:
            attributes[last_key] += line[1:]
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            last_key = key.strip()
            attributes[last_key] = value.strip()
    return attributes


def is_signed_jar(names: list[str]) -> bool:
    return any(re.match(r"^META-INF/[^/]+\.(SF|RSA|DSA|EC)$", name, re.IGNORECASE) for name in names)


def verify_plugin_file(path: Path, plugin_id: str, release: dict, file_name: str, report: Report) -> None:
    """Checks that the downloaded file is a plugin JAR matching the manifest entry. The JAR is never executed."""
    version = release["version"]
    try:
        with zipfile.ZipFile(path) as jar:
            names = jar.namelist()
            if "META-INF/MANIFEST.MF" not in names:
                report.error(f"{version}: the plugin JAR does not contain a META-INF/MANIFEST.MF", file_name)
                return
            attributes = parse_jar_manifest(jar.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace"))
            corrupt = jar.testzip()
            if corrupt is not None:
                report.error(f"{version}: the plugin JAR is corrupt (first bad entry: {corrupt})", file_name)
    except zipfile.BadZipFile:
        report.error(f"{version}: the downloaded file is not a valid JAR file", file_name)
        return

    if attributes.get("Plugin-Id") != plugin_id:
        report.error(f"{version}: 'Plugin-Id' in the JAR manifest is '{attributes.get('Plugin-Id')}', "
                     f"expected '{plugin_id}'", file_name)
    if attributes.get("Plugin-Version") != version:
        report.error(f"{version}: 'Plugin-Version' in the JAR manifest is '{attributes.get('Plugin-Version')}', "
                     f"expected '{version}'", file_name)
    if not attributes.get("Plugin-Class"):
        report.error(f"{version}: the JAR manifest does not contain a 'Plugin-Class'", file_name)

    jar_requires = (attributes.get("Plugin-Requires") or "").strip()
    if not jar_requires:
        report.error(f"{version}: the JAR manifest does not contain 'Plugin-Requires' "
                     f"(set it to the minimum Gameyfin version, e.g. '>=2.5.1')", file_name)
    elif jar_requires != release["requires"].strip():
        report.error(f"{version}: 'requires' ({release['requires']}) does not match 'Plugin-Requires' in the JAR "
                     f"manifest ({jar_requires})", file_name)

    if is_signed_jar(names) and not plugin_id.startswith(RESERVED_ID_PREFIX):
        report.error(f"{version}: the plugin JAR is signed. Gameyfin only accepts signatures of the Gameyfin team, "
                     f"plugins signed with another key are marked as UNTRUSTED and can't be enabled. "
                     f"Please publish an unsigned JAR.", file_name)


def verify_release(plugin_id: str, release: dict, file_name: str, report: Report, allow_insecure: bool) -> None:
    version = release["version"]
    with tempfile.TemporaryDirectory(prefix="gameyfin-marketplace-") as tmp:
        target = Path(tmp) / f"{plugin_id}-{version}.jar"
        try:
            download(release["url"], MAX_PLUGIN_SIZE, target, allow_insecure)
        except DownloadError as e:
            report.error(f"{version}: download of {release['url']} failed: {e}", file_name)
            return

        actual = sha512_of(target)
        if actual != release["sha512sum"].lower():
            report.error(f"{version}: SHA-512 mismatch (manifest: {release['sha512sum']}, actual: {actual})",
                         file_name)
            return

        verify_plugin_file(target, plugin_id, release, file_name, report)


def verify_logo(url: str, file_name: str, report: Report, allow_insecure: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="gameyfin-marketplace-") as tmp:
        target = Path(tmp) / "logo"
        try:
            download(url, MAX_LOGO_SIZE, target, allow_insecure)
        except DownloadError as e:
            report.error(f"Download of logo {url} failed: {e}", file_name)
            return
        data = target.read_bytes()
        if not any(check(data) for check in LOGO_SIGNATURES.values()):
            report.error(f"The logo must be a PNG, JPEG, GIF, WebP or SVG image ({url})", file_name)


# ---------------------------------------------------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout


def git_show_json(repo: Path, ref: str, path: str) -> Any | None:
    """Returns the parsed JSON of path at ref, or None if the file doesn't exist (or is invalid) at ref."""
    try:
        return json.loads(git(repo, "show", f"{ref}:{path}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def git_changed_files(repo: Path, base_ref: str) -> list[tuple[str, str]]:
    """Returns (status, path) of the files changed between the merge base of base_ref and HEAD."""
    output = git(repo, "diff", "--name-status", "--no-renames", f"{base_ref}...HEAD")
    changes = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        changes.append((status[0], path))
    return changes
