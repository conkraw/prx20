"""Storage tests use synthetic archives and mocked GitHub responses only."""

import base64
import copy
import gzip
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

import requests
from cryptography.fernet import Fernet

from epa_storage import ConfigurationError, ConflictError, GitHubStore, StorageError


def state():
    return {
        "schema_version": 1, "forms": {}, "evaluations": {},
        "conflicts": {}, "settings": {"academic_year_start_month": 7},
        "updated_at": None,
    }


def sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def response(status=200, obj=None, headers=None):
    result = Mock(spec=requests.Response)
    result.status_code = status
    result.headers = headers or {}
    result.json.return_value = obj if obj is not None else {}
    return result


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key()
        self.store = GitHubStore({
            "token": "synthetic_token", "repo": "example/epa", "branch": "main",
            "path": "data/epa_store.enc",
        }, self.key)
        self.network = Mock()
        self.store._session.request = self.network

    def contents(self, data, inline=True):
        return response(obj={
            "type": "file", "sha": sha(data), "size": len(data),
            "encoding": "base64" if inline else "none",
            "content": base64.b64encode(data).decode() if inline else "",
        })

    def test_full_state_roundtrip_is_encrypted(self):
        original = state()
        original["evaluations"]["example"] = {"resident": "Synthetic Example", "score": 4}
        encrypted = self.store.encode(original)
        self.assertNotIn(b"Synthetic Example", encrypted)
        self.assertEqual(self.store.decode(encrypted), original)
        self.assertNotEqual(self.store.encode(original), encrypted)

    def test_wrong_key_and_corruption_fail_closed(self):
        encrypted = self.store.encode(state())
        other = GitHubStore({"token": "synthetic_token", "repo": "example/epa"}, Fernet.generate_key())
        with self.assertRaisesRegex(StorageError, "could not be decrypted"):
            other.decode(encrypted)
        with self.assertRaises(StorageError):
            self.store.decode(encrypted[:-8] + b"changed!")

    def test_invalid_schema_rejected(self):
        original = state()
        original["schema_version"] = 9
        with self.assertRaisesRegex(StorageError, "schema"):
            self.store.decode(self.store.encode(original))
        original = state()
        original["evaluations"] = []
        with self.assertRaisesRegex(StorageError, "required data"):
            self.store.decode(self.store.encode(original))

    def test_corrupt_compressed_payload_rejected(self):
        encrypted = Fernet(self.key).encrypt(b"EPA-ARCHIVE-1\nnot a gzip payload")
        with self.assertRaisesRegex(StorageError, "damaged"):
            self.store.decode(encrypted)

    def test_nonfinite_numbers_rejected(self):
        original = state()
        original["bad_number"] = float("nan")
        with self.assertRaisesRegex(StorageError, "cannot be saved"):
            self.store.encode(original)
        malformed = b'{"schema_version":1,"forms":{},"evaluations":{},"conflicts":{},"settings":{},"bad":NaN}'
        encrypted = Fernet(self.key).encrypt(b"EPA-ARCHIVE-1\n" + gzip.compress(malformed))
        with self.assertRaises(StorageError):
            self.store.decode(encrypted)

    def test_inline_load_verifies_git_sha(self):
        encrypted = self.store.encode(state())
        self.network.return_value = self.contents(encrypted)
        loaded, version = self.store.load()
        self.assertEqual(loaded, state())
        self.assertEqual(version, sha(encrypted))
        self.assertEqual(self.store.last_sha, version)
        self.assertEqual(self.network.call_count, 1)
        self.assertEqual(self.network.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(self.network.call_args.kwargs["params"], {"ref": "main"})

    def test_large_file_uses_exact_immutable_blob(self):
        encrypted = self.store.encode(state())
        self.network.side_effect = [
            self.contents(encrypted, inline=False),
            response(obj={"sha": sha(encrypted), "encoding": "base64", "content": base64.b64encode(encrypted).decode()}),
        ]
        loaded, version = self.store.load()
        self.assertEqual(loaded, state())
        self.assertTrue(self.network.call_args.args[1].endswith("/git/blobs/" + version))
        self.assertEqual(self.network.call_count, 2)

    def test_blob_mismatch_rejected(self):
        encrypted = self.store.encode(state())
        bad_contents = self.contents(encrypted)
        bad_contents.json.return_value["sha"] = "a" * 40
        self.network.return_value = bad_contents
        with self.assertRaisesRegex(StorageError, "did not match"):
            self.store.load()

    def test_missing_archive_requires_repo_and_branch_access(self):
        self.network.side_effect = [response(404), response(200), response(200)]
        self.assertEqual(self.store.load(), (None, None))
        self.assertEqual(self.network.call_count, 3)

    def test_missing_repo_not_treated_as_empty_archive(self):
        self.network.side_effect = [response(404), response(404)]
        with self.assertRaisesRegex(ConfigurationError, "repository"):
            self.store.load()
        self.assertEqual(self.network.call_count, 2)

    def test_invalid_branch_not_treated_as_empty_archive(self):
        self.network.side_effect = [response(404), response(200), response(404)]
        with self.assertRaisesRegex(ConfigurationError, "branch"):
            self.store.load()

    def test_permissions_error_has_no_server_body(self):
        self.network.return_value = response(403, {"message": "DO_NOT_DISCLOSE"})
        with self.assertRaises(StorageError) as caught:
            self.store.load()
        self.assertNotIn("DO_NOT_DISCLOSE", str(caught.exception))
        self.assertNotIn("synthetic_token", str(caught.exception))

    def test_redirect_is_not_followed(self):
        self.network.return_value = response(301)
        with self.assertRaises(ConfigurationError):
            self.store.load()
        self.assertFalse(self.network.call_args.kwargs["allow_redirects"])

    def test_invalid_json_response_is_safe_error(self):
        bad = response()
        bad.json.side_effect = ValueError("SENSITIVE SERVER CONTENT")
        self.network.return_value = bad
        with self.assertRaises(StorageError) as caught:
            self.store.load()
        self.assertNotIn("SENSITIVE", str(caught.exception))

    def test_save_contains_encrypted_content_and_exact_prior_version(self):
        original = state()
        original["evaluations"]["test"] = {"resident": "Synthetic Resident"}

        def saved(method, url, **kwargs):
            body = kwargs["json"]
            raw = base64.b64decode(body["content"])
            self.assertEqual(self.store.decode(raw), original)
            self.assertNotIn("Synthetic Resident", json.dumps(body))
            self.assertEqual(body["sha"], "b" * 40)
            self.assertEqual(body["message"], "Update encrypted EPA archive")
            return response(200, {"content": {"sha": sha(raw)}})

        self.network.side_effect = saved
        returned = self.store.save(original, "b" * 40)
        self.assertEqual(returned, self.store.last_sha)

    def test_creation_omits_sha(self):
        def saved(method, url, **kwargs):
            body = kwargs["json"]
            self.assertNotIn("sha", body)
            return response(201, {"content": {"sha": sha(base64.b64decode(body["content"]))}})
        self.network.side_effect = saved
        self.store.save(state(), None)

    def test_save_conflict_does_not_force_overwrite(self):
        self.network.return_value = response(409)
        with self.assertRaises(ConflictError):
            self.store.save(state(), "a" * 40)
        self.assertEqual(self.network.call_count, 1)

    def test_creation_race_is_a_conflict(self):
        self.network.return_value = response(422)
        with self.assertRaises(ConflictError):
            self.store.save(state(), None)

    def test_transaction_reloads_and_reapplies_without_lost_update(self):
        before = state()
        concurrent = copy.deepcopy(before)
        concurrent["evaluations"]["other_user"] = {"value": 1}
        self.store.load = Mock(side_effect=[(before, "a" * 40), (concurrent, "b" * 40)])
        self.store.save = Mock(side_effect=[ConflictError("race"), "c" * 40])

        def add_mine(current):
            current["evaluations"]["mine"] = {"value": 2}
            return current, {"added": 1}

        result, stats = self.store.transaction(add_mine)
        self.assertEqual(set(result["evaluations"]), {"other_user", "mine"})
        self.assertEqual(stats, {"added": 1})
        self.assertEqual(self.store.save.call_args.kwargs["expected_sha"], "b" * 40)
        self.assertEqual(before["evaluations"], {})

    def test_duplicate_transaction_has_no_write(self):
        initial = state()
        self.store.load = Mock(return_value=(initial, "a" * 40))
        self.store.save = Mock()
        result, stats = self.store.transaction(lambda current: (current, {"duplicates": 1}))
        self.assertEqual(result, initial)
        self.assertEqual(stats["duplicates"], 1)
        self.store.save.assert_not_called()

    def test_transaction_failures_do_not_claim_save(self):
        self.store.load = Mock(return_value=(state(), "a" * 40))
        self.store.save = Mock(side_effect=ConflictError("race"))
        def transform(current):
            current["updated_at"] = "2026-11-02"
            return current, None
        with self.assertRaisesRegex(ConflictError, "have not been saved"):
            self.store.transaction(transform, attempts=2)
        self.assertEqual(self.store.save.call_count, 2)

    def test_http_timeout_is_reported_without_secret(self):
        self.network.side_effect = requests.Timeout("synthetic_token")
        with self.assertRaises(StorageError) as caught:
            self.store.load()
        self.assertNotIn("synthetic_token", str(caught.exception))

    def test_configuration_rejects_invalid_targets(self):
        for repo in ("https://evil.invalid/a/b", "../repo", "owner/repo/more"):
            with self.subTest(repo=repo), self.assertRaises(ConfigurationError):
                GitHubStore({"token": "synthetic_token", "repo": repo}, self.key)
        for path in ("/tmp/a", "../a", "data/../a", "data//a", "data\\a"):
            with self.subTest(path=path), self.assertRaises(ConfigurationError):
                GitHubStore({"token": "synthetic_token", "repo": "example/epa", "path": path}, self.key)


if __name__ == "__main__":
    unittest.main()
