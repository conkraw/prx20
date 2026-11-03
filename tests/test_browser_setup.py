"""Check the local HTML setup helper against the application's Python crypto."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import pytest
from cryptography.fernet import Fernet

from epa_auth import verify_password


PAGE = Path(__file__).resolve().parents[1] / "setup_secrets.html"


def _run_helper(requests):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is only needed to verify the browser helper.")
    script = re.search(r'<script id="setup-script">(.*?)</script>', PAGE.read_text(), re.S).group(1)
    harness = r'''
const vm = require("node:vm")
const { webcrypto } = require("node:crypto")
const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"))
const context = vm.createContext({
  crypto: webcrypto, TextEncoder, TextDecoder,
  btoa: value => Buffer.from(value, "binary").toString("base64")
})
vm.runInContext(payload.script, context)
Promise.all(payload.requests.map(async args => {
  try { return { value: await context.createSettings(...args) } }
  catch (error) { return { error: error.message } }
})).then(results => process.stdout.write(JSON.stringify(results)))
'''
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps({"script": script, "requests": requests}),
        text=True, capture_output=True, check=True, timeout=20,
    )
    return json.loads(result.stdout)


def test_browser_hashes_and_keys_work_with_python_and_are_fresh():
    password = "A synthetic browser passphrase"
    output = _run_helper([[password, password, "example/epa-data", "github_pat_EXAMPLE"]] * 2)
    settings = [tomllib.loads(item["value"]) for item in output]
    for config in settings:
        assert verify_password(password, config["app"]["password_hash"])
        assert not verify_password(password + "!", config["app"]["password_hash"])
        cipher = Fernet(config["security"]["fernet_key"])
        assert cipher.decrypt(cipher.encrypt(b"synthetic data")) == b"synthetic data"
        assert config["github"]["repo"] == "example/epa-data"
        assert config["github"]["token"] == "github_pat_EXAMPLE"
        assert config["github"]["path"] == "data/epa_store.enc"
        assert password not in output[0]["value"]
    assert settings[0]["app"]["password_hash"] != settings[1]["app"]["password_hash"]
    assert settings[0]["security"]["fernet_key"] != settings[1]["security"]["fernet_key"]


def test_browser_helper_validates_inputs_and_leaves_safe_placeholders():
    password = "Another synthetic passphrase"
    special_password = "\U0001f427" * 12
    answers = _run_helper([
        [password, password, "", ""],
        ["short", "short", "", ""],
        [password, "Different password", "", ""],
        [password, password, "https://github.com/example/data", ""],
        [special_password, special_password, "", ""],
        ["\U0001f427" * 6, "\U0001f427" * 6, "", ""],
        [password + "\ud800", password + "\ud800", "", ""],
        [password, password, "example/data", 'synthetic"token\\with\\quotes'],
    ])
    config = tomllib.loads(answers[0]["value"])
    assert config["github"]["token"] == "PASTE_YOUR_GITHUB_TOKEN"
    assert config["github"]["repo"] == "YOUR_USERNAME/epa-data"
    for position in (1, 2, 3, 5, 6):
        assert "error" in answers[position]
    assert verify_password(special_password, tomllib.loads(answers[4]["value"])["app"]["password_hash"])
    assert tomllib.loads(answers[7]["value"])["github"]["token"] == 'synthetic"token\\with\\quotes'


def test_browser_helper_is_self_contained_and_forbids_network_connections():
    html = PAGE.read_text()
    assert "connect-src 'none'" in html
    assert "form-action 'none'" in html
    assert "default-src 'none'" in html
    assert not re.search(r"<(?:script|link|img)[^>]+(?:src|href)=", html, re.I)
    assert not re.search(r"fetch\s*\(|XMLHttpRequest|WebSocket|localStorage|sessionStorage|indexedDB|console\.", html)
