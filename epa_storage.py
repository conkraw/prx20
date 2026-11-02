"""Encrypted, optimistic-concurrency storage in a GitHub repository.

Only encrypted bytes leave the server. The transform passed to transaction()
must be deterministic and free of external side effects because it may be
reapplied to a newer archive after a concurrent update.
"""

from __future__ import annotations

import base64
import binascii
import copy
import gzip
import hashlib
import io
import json
import re
import zlib
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken


class StorageError(RuntimeError):
    """An archive operation failed without exposing transport details."""


class ConflictError(StorageError):
    """The archive changed after it was loaded."""


class ConfigurationError(StorageError):
    """Required storage settings are missing or invalid."""


_API_ROOT = "https://api.github.com"
_API_VERSION = "2026-03-10"
_PAYLOAD_MAGIC = b"EPA-ARCHIVE-1\n"
_MAX_ENCRYPTED_BYTES = 25 * 1024 * 1024
_MAX_JSON_BYTES = 100 * 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _json_bytes(state: dict[str, Any]) -> bytes:
    if not isinstance(state, dict):
        raise StorageError("The archive must be a JSON object.")
    try:
        result = json.dumps(
            state, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise StorageError("The archive contains a value that cannot be saved.") from exc
    if len(result) > _MAX_JSON_BYTES:
        raise StorageError("The archive is too large for this storage configuration.")
    return result


def _git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


class GitHubStore:
    """Store one encrypted archive using GitHub's Contents API.

    config is the [github] section from Streamlit Secrets. Only github.com is
    supported. No unauthenticated download URL returned by the API is used.
    """

    def __init__(self, config: Mapping[str, Any], fernet_key: str | bytes):
        self.repo = str(config.get("repo", "")).strip()
        self.branch = str(config.get("branch", "main")).strip()
        self.path = str(config.get("path", "data/epa_store.enc")).strip()
        token = str(config.get("token", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", self.repo) or self.repo.split("/")[-1] in (".", ".."):
            raise ConfigurationError("Set github.repo to owner/repository in Streamlit Secrets.")
        if not token or token.lower().startswith(("replace", "your_", "paste_")):
            raise ConfigurationError("Set github.token in Streamlit Secrets.")
        if not self.branch or any(ord(c) < 32 for c in self.branch):
            raise ConfigurationError("Set a valid github.branch in Streamlit Secrets.")
        parts = self.path.split("/")
        if not self.path or any(p in ("", ".", "..") for p in parts) or "\\" in self.path:
            raise ConfigurationError("Set github.path to a relative archive file path.")
        if any(ord(c) < 32 for c in self.path):
            raise ConfigurationError("Set a valid github.path in Streamlit Secrets.")
        try:
            self._fernet = Fernet(fernet_key)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise ConfigurationError("Set a valid security.fernet_key in Streamlit Secrets.") from exc
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/vnd.github.object+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "EPA-Tracker/1.0",
        })
        self._base = f"{_API_ROOT}/repos/{self.repo}"
        self._contents_url = self._base + "/contents/" + quote(self.path, safe="/")
        self.last_sha: str | None = None

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        # URLs are generated only from fixed official API endpoints above.
        try:
            response = self._session.request(
                method, url, timeout=(10, 60), allow_redirects=False, **kwargs
            )
        except requests.RequestException as exc:
            raise StorageError("GitHub could not be reached. Your changes have not been confirmed as saved. Reload before retrying.") from exc
        if response.status_code in (401, 403):
            if response.headers.get("X-RateLimit-Remaining") == "0":
                raise StorageError("GitHub's request limit was reached. Wait before retrying.")
            raise StorageError("GitHub denied access. Check the token, repository permissions, and any organization approval.")
        if response.status_code == 429:
            raise StorageError("GitHub temporarily limited requests. Wait before retrying.")
        if response.status_code >= 500:
            raise StorageError("GitHub is temporarily unavailable. Your changes have not been confirmed as saved.")
        if 300 <= response.status_code < 400:
            raise ConfigurationError("GitHub redirected this repository. Update github.repo to its current owner and name.")
        return response

    @staticmethod
    def _object(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise StorageError("GitHub returned an unreadable response. No archive was replaced.") from exc
        if not isinstance(payload, dict):
            raise StorageError("The configured archive path is not a file.")
        return payload

    def _verify_missing_file(self) -> None:
        """A 404 is an empty archive only after repo and branch access succeeds."""
        repo = self._request("GET", self._base)
        if repo.status_code != 200:
            raise ConfigurationError("The GitHub repository is unavailable. Check github.repo and the token's access.")
        branch = self._request("GET", self._base + "/branches/" + quote(self.branch, safe=""))
        if branch.status_code != 200:
            raise ConfigurationError("The configured GitHub branch does not exist or is inaccessible. Create the branch first.")

    def encode(self, state: dict[str, Any]) -> bytes:
        """Create encrypted bytes, also usable for an encrypted backup."""
        serialized = _json_bytes(state)
        encrypted = self._fernet.encrypt(_PAYLOAD_MAGIC + gzip.compress(serialized, mtime=0))
        if len(encrypted) > _MAX_ENCRYPTED_BYTES:
            raise StorageError("The encrypted archive exceeds this app's 25 MB storage limit.")
        return encrypted

    def decode(self, encrypted: bytes) -> dict[str, Any]:
        """Validate and decrypt an archive, failing closed on any corruption."""
        if len(encrypted) > _MAX_ENCRYPTED_BYTES:
            raise StorageError("The encrypted archive exceeds this app's 25 MB storage limit.")
        try:
            plaintext = self._fernet.decrypt(encrypted)
        except (InvalidToken, ValueError, TypeError) as exc:
            raise StorageError("The archive could not be decrypted. Check the existing encryption key. No data has been overwritten.") from exc
        if not plaintext.startswith(_PAYLOAD_MAGIC):
            raise StorageError("The archive has an unsupported format. No data has been overwritten.")
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(plaintext[len(_PAYLOAD_MAGIC):])) as compressed:
                payload = compressed.read(_MAX_JSON_BYTES + 1)
            if len(payload) > _MAX_JSON_BYTES:
                raise StorageError("The decrypted archive exceeds this app's size limit.")
            state = json.loads(payload.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        except (OSError, EOFError, ValueError, UnicodeError, zlib.error) as exc:
            raise StorageError("The archive is damaged or unreadable. No data has been overwritten.") from exc
        if not isinstance(state, dict):
            raise StorageError("The archive does not contain a valid state object.")
        if state.get("schema_version") != 1:
            raise StorageError("The archive uses an unsupported schema version. Update the app before loading it.")
        for key in ("forms", "evaluations", "conflicts", "settings"):
            if not isinstance(state.get(key), dict):
                raise StorageError("The archive is missing required data. No data has been overwritten.")
        return state

    @staticmethod
    def _decode_base64(value: Any) -> bytes:
        if not isinstance(value, str):
            raise StorageError("GitHub returned unreadable archive bytes.")
        try:
            # GitHub wraps base64 content over multiple lines.
            return base64.b64decode("".join(value.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise StorageError("GitHub returned unreadable archive bytes.") from exc

    def load(self) -> tuple[dict[str, Any] | None, str | None]:
        response = self._request("GET", self._contents_url, params={"ref": self.branch})
        if response.status_code == 404:
            self._verify_missing_file()
            self.last_sha = None
            return None, None
        if response.status_code != 200:
            raise StorageError("GitHub could not load the archive. Check the configured file path.")
        obj = self._object(response)
        sha = obj.get("sha")
        if obj.get("type") != "file" or not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise StorageError("The configured archive path does not point to a regular file.")
        size = obj.get("size")
        if not isinstance(size, int) or size < 0 or size > _MAX_ENCRYPTED_BYTES:
            raise StorageError("The encrypted archive exceeds this app's size limit or has invalid metadata.")
        if obj.get("encoding") == "base64" and obj.get("content"):
            encrypted = self._decode_base64(obj["content"])
        else:
            # Read the exact immutable blob. Do not fetch a potentially newer
            # branch version or follow a download_url from an API response.
            blob_response = self._request("GET", self._base + "/git/blobs/" + sha)
            if blob_response.status_code != 200:
                raise StorageError("GitHub could not retrieve the archive's saved version.")
            blob = self._object(blob_response)
            if blob.get("sha") != sha or blob.get("encoding") != "base64":
                raise StorageError("GitHub returned a different archive version. Reload before retrying.")
            encrypted = self._decode_base64(blob.get("content"))
        if len(encrypted) != size or _git_blob_sha(encrypted) != sha:
            raise StorageError("The downloaded archive did not match its saved version. Reload before retrying.")
        state = self.decode(encrypted)
        self.last_sha = sha
        return state, sha

    def save(self, state: dict[str, Any], expected_sha: str | None) -> str:
        if expected_sha is not None and not _SHA_RE.fullmatch(expected_sha):
            raise StorageError("The saved archive version is invalid. Reload before saving.")
        content = self.encode(state)
        body: dict[str, Any] = {
            "message": "Update encrypted EPA archive",
            "branch": self.branch,
            "content": base64.b64encode(content).decode("ascii"),
        }
        if expected_sha is not None:
            body["sha"] = expected_sha
        response = self._request("PUT", self._contents_url, json=body)
        if response.status_code == 409:
            raise ConflictError("The archive changed in another session. Reload and merge before saving.")
        if response.status_code == 422 and expected_sha is None:
            # Two users may create the previously absent file at once. A later
            # transaction reload either merges it or reports a final conflict.
            raise ConflictError("The archive may have been created in another session. Reload before saving.")
        if response.status_code not in (200, 201):
            raise StorageError("GitHub did not confirm the save. Check write permissions, branch rules, and the archive path.")
        obj = self._object(response)
        sha = obj.get("content", {}).get("sha") if isinstance(obj.get("content"), dict) else None
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha) or sha != _git_blob_sha(content):
            raise StorageError("GitHub did not return a valid saved version. Reload to check whether the changes were saved.")
        self.last_sha = sha
        return sha

    def transaction(
        self,
        transform: Callable[[dict[str, Any] | None], tuple[dict[str, Any], Any]],
        attempts: int = 3,
    ) -> tuple[dict[str, Any], Any]:
        """Load, transform, and compare-and-swap, reapplying after conflicts."""
        if not 1 <= attempts <= 5:
            raise ValueError("attempts must be between 1 and 5")
        for attempt in range(attempts):
            current, sha = self.load()
            before = _json_bytes(current) if current is not None else None
            updated, result = transform(copy.deepcopy(current))
            after = _json_bytes(updated)
            if before == after:
                return updated, result
            try:
                self.save(updated, expected_sha=sha)
                return updated, result
            except ConflictError:
                if attempt + 1 == attempts:
                    raise ConflictError("Other sessions are still updating the archive. Reload and retry. Your changes have not been saved.") from None
        raise AssertionError("unreachable")
