"""Generate a private Streamlit Secrets template in your local terminal.

Run: python setup_secrets.py
Do not commit the generated output. Keep the encryption key permanently.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from cryptography.fernet import Fernet

from epa_auth import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate EPA Tracker secrets locally.")
    parser.add_argument("--password-only", action="store_true", help="Generate only a new password hash. Preserve your existing encryption key.")
    args = parser.parse_args()
    try:
        password = getpass.getpass("Choose an app password or passphrase (12 or more characters): ")
        confirmation = getpass.getpass("Enter it again: ")
        if password != confirmation:
            print("Passwords do not match. Nothing was generated.", file=sys.stderr)
            return 1
        encoded = hash_password(password)
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) or "Cancelled.", file=sys.stderr)
        return 1
    if args.password_only:
        print('password_hash = "' + encoded + '"')
        return 0
    key = Fernet.generate_key().decode("ascii")
    print("\nCopy the TOML below into Streamlit Secrets. Keep a secure backup.", file=sys.stderr)
    print("For an existing archive, keep its existing encryption key. A new key cannot decrypt old data.", file=sys.stderr)
    print(f'''[app]
password_hash = "{encoded}"
session_minutes = 30

[github]
token = "PASTE_YOUR_GITHUB_TOKEN"
repo = "YOUR_USERNAME/YOUR_DATA_REPOSITORY"
branch = "main"
path = "data/epa_store.enc"

[security]
fernet_key = "{key}"
''')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
