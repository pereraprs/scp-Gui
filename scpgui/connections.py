"""
connections.py
---------------
Manages saved connection profiles (e.g. "Host PC -> Build VM",
"VM1 -> VM2") so the user doesn't have to re-enter host/user/auth
details every time. Profiles are stored as JSON under
~/.config/scp-gui/connections.json.

For security, PASSWORDS ARE NEVER SAVED to disk -- only which auth
method a profile uses (password vs. private key) and, for key auth,
the path to the key file. If a profile uses password auth, the user
is prompted for the password each time they connect.
"""

import json
import os

CONFIG_DIR = os.path.expanduser("~/.config/scp-gui")
CONFIG_FILE = os.path.join(CONFIG_DIR, "connections.json")


class ConnectionProfile:
    def __init__(self, name, host, port, username, auth_type, key_path=""):
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.auth_type = auth_type  # "password" or "key"
        self.key_path = key_path

    def to_dict(self):
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "key_path": self.key_path,
        }

    @staticmethod
    def from_dict(d):
        return ConnectionProfile(
            name=d.get("name", ""),
            host=d.get("host", ""),
            port=d.get("port", 22),
            username=d.get("username", ""),
            auth_type=d.get("auth_type", "password"),
            key_path=d.get("key_path", ""),
        )


def load_profiles():
    """Return a list of ConnectionProfile objects, or [] if none saved."""
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        return [ConnectionProfile.from_dict(d) for d in data]
    except (json.JSONDecodeError, OSError):
        return []


def save_profiles(profiles):
    """Persist the full list of ConnectionProfile objects to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump([p.to_dict() for p in profiles], f, indent=2)
    # Config may contain host/username details; keep it user-readable only.
    os.chmod(CONFIG_FILE, 0o600)


def upsert_profile(profile):
    """Add a new profile or replace an existing one with the same name."""
    profiles = load_profiles()
    profiles = [p for p in profiles if p.name != profile.name]
    profiles.append(profile)
    save_profiles(profiles)
    return profiles


def delete_profile(name):
    profiles = load_profiles()
    profiles = [p for p in profiles if p.name != name]
    save_profiles(profiles)
    return profiles
