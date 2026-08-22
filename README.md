# SCP GUI

A simple dual-pane GUI application for transferring files securely over
SSH (SCP/SFTP), built for Debian-based Linux systems (Debian, Ubuntu, Mint,
etc.). Think of it as a lightweight WinSCP alternative written in Python.

## Features
- Connect via password or SSH private key
- Browse local and remote file systems side by side
- Upload / download single files or whole directories (recursive)
- Create folders, delete files/folders, navigate remote directories
- Background threads keep the UI responsive during transfers, with a
  live progress bar
- Saved connection profiles — name a link (e.g. "PC → Build VM",
  "VM1 → VM2") and reconnect without retyping host/user/auth; works for
  host↔VM and VM↔VM links alike, with password- and key-based auth
  side by side. Passwords are never written to disk, only the auth
  method used.
- Dark theme by default, with a one-click toggle to light mode
- Custom app icon and `.desktop` entry (shows up in your applications
  menu after installing the `.deb`)

## Project layout
```
scp-gui/
├── scpgui/
│   ├── __init__.py
│   ├── ssh_client.py    # paramiko-based SFTP/SCP backend
│   ├── connections.py   # saved connection profiles
│   ├── theme.py         # dark/light theme switching
│   ├── main_window.py   # PyQt5 GUI
│   └── main.py          # entry point
├── packaging/
│   ├── build-deb.sh     # rebuilds the .deb from source
│   └── debian/          # control, postinst, .desktop, copyright, icon
├── requirements.txt
├── setup.py
├── LICENSE
└── README.md
```

## 1. Install dependencies (Debian/Ubuntu)

PyQt5 needs a couple of system libraries on a fresh Debian system:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv \
    libxcb-cursor0 libxkbcommon-x11-0

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Run it

```bash
python3 -m scpgui.main
```

## 3. Install as a command (optional)

```bash
pip install .
scp-gui
```

## 4. Package as a .deb

```bash
./packaging/build-deb.sh
sudo apt install python3-pyqt5 python3-paramiko
sudo dpkg -i build/scp-gui_0.1.0_all.deb
```

The script assembles a standard Debian binary package tree (control file,
launcher script, `.desktop` entry, copyright) from `packaging/debian/` and
builds it with `dpkg-deb`. Bump the version in
`packaging/debian/control` before re-running to cut a new release.

## Notes
- Host keys are auto-accepted on first connect (`AutoAddPolicy`) for
  simplicity — for stricter environments, swap this for a known_hosts
  check in `ssh_client.py`.
- The app uses SFTP under the hood (an SSH sub-protocol) rather than the
  legacy SCP protocol, because SFTP supports directory listing and richer
  error handling — from a user's perspective it's the same "secure copy"
  workflow.
- Saved connections live in `~/.config/scp-gui/connections.json`, local
  to whichever machine the app is installed on — copying that file
  between a host and a VM carries your saved links over too.
