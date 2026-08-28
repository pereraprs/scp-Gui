"""
main_window.py
---------------
PyQt5 GUI: a dual-pane file transfer manager for SCP/SFTP.

Two modes, switchable from the top of the window:
- "PC <-> Server": the left pane is your local filesystem, the right
  pane is one remote server. This is the original mode.
- "Server <-> Server": BOTH panes are remote connections (their own
  host/user/auth fields, their own directory listing). Transfers move
  files directly between the two servers -- there's no direct
  server-to-server copy in the SFTP protocol, so under the hood this
  downloads to a local temp folder and re-uploads to the other side,
  then cleans the temp folder up. From the user's point of view it's
  a single "Transfer" between two VMs.

Each remote pane (local-mode's single one, or both in server-mode) is
a flat "current directory" listing with an Up button and double-click
navigation, with folder/file icons like a normal file manager.
"""

import os
import shutil
import tempfile

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLineEdit, QSpinBox, QPushButton, QLabel, QSplitter, QTreeView,
    QTreeWidget, QTreeWidgetItem, QFileSystemModel,
    QProgressBar, QMessageBox, QInputDialog, QFileDialog, QStatusBar,
    QComboBox, QApplication, QStyle,
)

from scpgui.ssh_client import SCPClient, SSHConnectionError
from scpgui import connections
from scpgui import theme


# ---------------------------------------------------------------------- #
# Background worker threads (keep the GUI responsive)
# ---------------------------------------------------------------------- #
class ConnectWorker(QThread):
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, client, host, port, username, password, key_path):
        super().__init__()
        self.client = client
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path

    def run(self):
        try:
            self.client.connect(
                self.host, self.port, self.username,
                password=self.password or None,
                key_path=self.key_path or None,
            )
            self.finished.emit(True, f"Connected to {self.username}@{self.host}")
        except SSHConnectionError as exc:
            self.finished.emit(False, str(exc))


class TransferWorker(QThread):
    """Local <-> remote transfer (used in 'PC <-> Server' mode)."""
    progress = pyqtSignal(int, int)   # bytes done, total bytes
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, action, client, src, dst, is_dir):
        super().__init__()
        self.action = action  # "upload" or "download"
        self.client = client
        self.src = src
        self.dst = dst
        self.is_dir = is_dir

    def _callback(self, done, total):
        self.progress.emit(done, total)

    def run(self):
        try:
            if self.action == "upload":
                if self.is_dir:
                    self.client.upload_directory(self.src, self.dst, self._callback)
                else:
                    self.client.upload(self.src, self.dst, self._callback)
            else:
                if self.is_dir:
                    self.client.download_directory(self.src, self.dst, self._callback)
                else:
                    self.client.download(self.src, self.dst, self._callback)
            self.finished.emit(True, "Transfer complete")
        except (SSHConnectionError, IOError, OSError) as exc:
            self.finished.emit(False, str(exc))


class RelayTransferWorker(QThread):
    """Server <-> server transfer (used in 'Server <-> Server' mode).

    SFTP has no server-to-server copy, so this downloads the source to
    a local temp folder, uploads it to the destination, then deletes
    the temp folder -- invisible to the user, who just sees "Transfer".
    """
    progress = pyqtSignal(int, int)   # combined progress, 0-100 scaled to "total"=100
    finished = pyqtSignal(bool, str)

    def __init__(self, client_from, client_to, src_path, dst_path, is_dir):
        super().__init__()
        self.client_from = client_from
        self.client_to = client_to
        self.src_path = src_path
        self.dst_path = dst_path
        self.is_dir = is_dir

    def _download_callback(self, done, total):
        pct = int(done * 50 / total) if total else 0
        self.progress.emit(pct, 100)

    def _upload_callback(self, done, total):
        pct = 50 + int(done * 50 / total) if total else 50
        self.progress.emit(pct, 100)

    def run(self):
        tmp_dir = tempfile.mkdtemp(prefix="scpgui-relay-")
        try:
            name = os.path.basename(self.src_path.rstrip("/")) or "transfer"
            local_tmp = os.path.join(tmp_dir, name)

            if self.is_dir:
                self.client_from.download_directory(self.src_path, local_tmp,
                                                      self._download_callback)
                self.client_to.upload_directory(local_tmp, self.dst_path,
                                                  self._upload_callback)
            else:
                self.client_from.download(self.src_path, local_tmp,
                                            self._download_callback)
                self.client_to.upload(local_tmp, self.dst_path,
                                        self._upload_callback)
            self.finished.emit(True, "Transfer complete")
        except (SSHConnectionError, IOError, OSError) as exc:
            self.finished.emit(False, str(exc))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class RootToggleWorker(QThread):
    """Switches one pane's SFTP session between the normal login user
    and root (via sudo), or vice versa.

    When switching TO root with no password supplied yet, it first
    checks whether this account has passwordless sudo -- if so it
    elevates immediately; if not, it emits need_password so the GUI
    thread can pop up a prompt and re-run the worker with it.
    """
    finished = pyqtSignal(bool, str, bool)  # success, message, now_root
    need_password = pyqtSignal()

    def __init__(self, client, enable_root, sudo_password=None):
        super().__init__()
        self.client = client
        self.enable_root = enable_root
        self.sudo_password = sudo_password

    def run(self):
        try:
            if self.enable_root:
                if self.sudo_password is None:
                    if not self.client.check_passwordless_sudo():
                        self.need_password.emit()
                        return
                self.client.elevate_to_root(self.sudo_password)
                self.finished.emit(True, "Switched to root", True)
            else:
                self.client.drop_to_user()
                self.finished.emit(True, "Switched back to your normal user", False)
        except SSHConnectionError as exc:
            self.finished.emit(False, str(exc), self.client.root_mode)


# ---------------------------------------------------------------------- #
# Main window
# ---------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SCP GUI — Secure File Transfer")
        self.resize(1180, 680)

        self.mode = "local"  # "local" (PC<->Server) or "server" (Server<->Server)

        # Per-side remote state. Side "b" is always the right-hand server.
        # Side "a" is only used as a remote connection in "server" mode --
        # in "local" mode the left side is the local filesystem instead.
        self.panes = {
            "a": {"client": SCPClient(), "path": "/"},
            "b": {"client": SCPClient(), "path": "/"},
        }

        self._pending_transfer_side = None  # which pane to refresh after a transfer

        self._build_ui()
        self._reload_profiles()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        style = self.style()
        self._folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        self._file_icon = style.standardIcon(QStyle.SP_FileIcon)

        # --- mode + theme bar ---
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["PC \u2194 Server", "Server \u2194 Server"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)
        top_bar.addStretch()
        self.theme_btn = QPushButton("☀ Light mode")
        self.theme_btn.clicked.connect(self._toggle_theme)
        top_bar.addWidget(self.theme_btn)
        outer.addLayout(top_bar)

        # --- dual pane splitter ---
        splitter = QSplitter(Qt.Horizontal)

        # LEFT: stacked -- local filesystem browser, or a remote pane (side "a")
        self.local_container = self._build_local_pane()
        self.pane_widgets = {}
        pane_a_widget = self._build_remote_pane("a")
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.local_container)  # index 0
        self.left_stack.addWidget(pane_a_widget)          # index 1
        splitter.addWidget(self.left_stack)

        # MIDDLE: transfer buttons
        mid_container = QWidget()
        mid_layout = QVBoxLayout(mid_container)
        mid_layout.addStretch()
        self.upload_btn = QPushButton("Transfer →")
        self.upload_btn.clicked.connect(self._on_transfer_left_to_right)
        self.download_btn = QPushButton("← Transfer")
        self.download_btn.clicked.connect(self._on_transfer_right_to_left)
        mid_layout.addWidget(self.upload_btn)
        mid_layout.addWidget(self.download_btn)
        mid_layout.addStretch()
        splitter.addWidget(mid_container)

        # RIGHT: always a remote pane (side "b")
        pane_b_widget = self._build_remote_pane("b")
        splitter.addWidget(pane_b_widget)

        splitter.setSizes([420, 80, 420])
        outer.addWidget(splitter, stretch=1)

        # --- progress + status ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        outer.addWidget(self.progress_bar)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Not connected")

        self._set_pane_controls_enabled("b", False)
        self._set_pane_controls_enabled("a", False)

    # ------------------------------------------------------------------ #
    # Local filesystem pane (left side, "PC <-> Server" mode)
    # ------------------------------------------------------------------ #
    def _build_local_pane(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Local"))
        self.local_model = QFileSystemModel()
        self.local_model.setRootPath(os.path.expanduser("~"))
        self.local_view = QTreeView()
        self.local_view.setModel(self.local_model)
        self.local_view.setRootIndex(self.local_model.index(os.path.expanduser("~")))
        self.local_view.setColumnWidth(0, 250)
        layout.addWidget(self.local_view)
        return container

    # ------------------------------------------------------------------ #
    # Generic remote pane builder (used for side "a" and side "b")
    # ------------------------------------------------------------------ #
    def _build_remote_pane(self, side):
        w = {}
        container = QWidget()
        outer = QVBoxLayout(container)

        # saved connections
        profiles_bar = QHBoxLayout()
        profiles_bar.addWidget(QLabel("Saved:"))
        w["profiles_combo"] = QComboBox()
        w["profiles_combo"].setMinimumWidth(160)
        w["profiles_combo"].currentIndexChanged.connect(
            lambda idx, s=side: self._on_profile_selected(s, idx))
        profiles_bar.addWidget(w["profiles_combo"])
        save_btn = QPushButton("Save As...")
        save_btn.clicked.connect(lambda _, s=side: self._on_save_profile(s))
        profiles_bar.addWidget(save_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(lambda _, s=side: self._on_delete_profile(s))
        profiles_bar.addWidget(del_btn)
        outer.addLayout(profiles_bar)

        # connection bar
        conn_bar = QHBoxLayout()
        w["host_edit"] = QLineEdit()
        w["host_edit"].setPlaceholderText("host")
        w["port_edit"] = QSpinBox()
        w["port_edit"].setRange(1, 65535)
        w["port_edit"].setValue(22)
        w["user_edit"] = QLineEdit()
        w["user_edit"].setPlaceholderText("username")
        w["pass_edit"] = QLineEdit()
        w["pass_edit"].setPlaceholderText("password (blank for key)")
        w["pass_edit"].setEchoMode(QLineEdit.Password)
        w["key_edit"] = QLineEdit()
        w["key_edit"].setPlaceholderText("private key path")
        key_browse = QPushButton("...")
        key_browse.setMaximumWidth(30)
        key_browse.clicked.connect(lambda _, s=side: self._browse_key(s))
        w["connect_btn"] = QPushButton("Connect")
        w["connect_btn"].clicked.connect(lambda _, s=side: self._on_connect_clicked(s))

        conn_bar.addWidget(QLabel("Host:")); conn_bar.addWidget(w["host_edit"])
        conn_bar.addWidget(QLabel("Port:")); conn_bar.addWidget(w["port_edit"])
        conn_bar.addWidget(QLabel("User:")); conn_bar.addWidget(w["user_edit"])
        conn_bar.addWidget(QLabel("Pass:")); conn_bar.addWidget(w["pass_edit"])
        conn_bar.addWidget(QLabel("Key:")); conn_bar.addWidget(w["key_edit"])
        conn_bar.addWidget(key_browse)
        conn_bar.addWidget(w["connect_btn"])
        outer.addLayout(conn_bar)

        # path + tree
        w["path_label"] = QLabel(f"Remote ({side.upper()}): (not connected)")
        outer.addWidget(w["path_label"])
        w["tree"] = QTreeWidget()
        w["tree"].setHeaderLabels(["Name", "Size", "Type"])
        w["tree"].setColumnWidth(0, 200)
        w["tree"].itemDoubleClicked.connect(
            lambda item, col, s=side: self._on_remote_double_click(s, item, col))
        outer.addWidget(w["tree"])

        btns = QHBoxLayout()
        up_btn = QPushButton("Up")
        up_btn.clicked.connect(lambda _, s=side: self._remote_go_up(s))
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda _, s=side: self._refresh_remote(s))
        mkdir_btn = QPushButton("New Folder")
        mkdir_btn.clicked.connect(lambda _, s=side: self._on_mkdir(s))
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(lambda _, s=side: self._on_delete_remote(s))
        w["root_toggle_btn"] = QPushButton("Switch to Root")
        w["root_toggle_btn"].setToolTip(
            "Browse and modify the whole VM filesystem via sudo, not "
            "just what your login user can see. Needs sudo rights on "
            "the remote account."
        )
        w["root_toggle_btn"].clicked.connect(lambda _, s=side: self._on_root_toggle_clicked(s))
        for b in (up_btn, refresh_btn, mkdir_btn, delete_btn, w["root_toggle_btn"]):
            btns.addWidget(b)
        outer.addLayout(btns)

        self.pane_widgets[side] = w
        return container

    def _set_pane_controls_enabled(self, side, enabled):
        w = self.pane_widgets[side]
        w["tree"].setEnabled(enabled)
        w["root_toggle_btn"].setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Mode switching
    # ------------------------------------------------------------------ #
    def _on_mode_changed(self, index):
        self.mode = "server" if index == 1 else "local"
        self.left_stack.setCurrentIndex(1 if self.mode == "server" else 0)
        hint = ("Server \u2194 Server: connect both sides, then Transfer moves "
                "files directly between them." if self.mode == "server"
                else "PC \u2194 Server: left is your local files, right is the server.")
        self.status_bar.showMessage(hint)

    # ------------------------------------------------------------------ #
    # Saved connection profiles (shared store, used by either side)
    # ------------------------------------------------------------------ #
    def _reload_profiles(self):
        self._profiles = connections.load_profiles()
        for side in ("a", "b"):
            combo = self.pane_widgets[side]["profiles_combo"]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("-- select a saved connection --")
            for p in self._profiles:
                combo.addItem(p.name)
            combo.blockSignals(False)

    def _on_profile_selected(self, side, index):
        if index <= 0:
            return
        profile = self._profiles[index - 1]
        w = self.pane_widgets[side]
        w["host_edit"].setText(profile.host)
        w["port_edit"].setValue(profile.port)
        w["user_edit"].setText(profile.username)
        w["pass_edit"].clear()
        w["key_edit"].setText(profile.key_path if profile.auth_type == "key" else "")

    def _on_save_profile(self, side):
        w = self.pane_widgets[side]
        host = w["host_edit"].text().strip()
        username = w["user_edit"].text().strip()
        if not host or not username:
            QMessageBox.warning(self, "Missing info", "Fill in host and username first.")
            return
        default_name = f"{username}@{host}"
        name, ok = QInputDialog.getText(self, "Save connection", "Name:", text=default_name)
        if not (ok and name):
            return
        key_path = w["key_edit"].text().strip()
        auth_type = "key" if key_path else "password"
        profile = connections.ConnectionProfile(
            name=name, host=host, port=w["port_edit"].value(),
            username=username, auth_type=auth_type, key_path=key_path,
        )
        connections.upsert_profile(profile)
        self._reload_profiles()
        w["profiles_combo"].setCurrentText(name)
        self.status_bar.showMessage(f"Saved connection '{name}'")

    def _on_delete_profile(self, side):
        combo = self.pane_widgets[side]["profiles_combo"]
        index = combo.currentIndex()
        if index <= 0:
            return
        profile = self._profiles[index - 1]
        confirm = QMessageBox.question(self, "Delete connection",
                                        f"Delete saved connection '{profile.name}'?")
        if confirm == QMessageBox.Yes:
            connections.delete_profile(profile.name)
            self._reload_profiles()

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #
    def _toggle_theme(self):
        app = QApplication.instance()
        dark = getattr(self, "dark_mode", True)
        self.dark_mode = not dark
        if self.dark_mode:
            theme.apply_dark_theme(app)
            self.theme_btn.setText("☀ Light mode")
        else:
            theme.apply_light_theme(app)
            self.theme_btn.setText("🌙 Dark mode")

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #
    def _browse_key(self, side):
        path, _ = QFileDialog.getOpenFileName(self, "Select private key",
                                               os.path.expanduser("~/.ssh"))
        if path:
            self.pane_widgets[side]["key_edit"].setText(path)

    def _on_connect_clicked(self, side):
        w = self.pane_widgets[side]
        host = w["host_edit"].text().strip()
        port = w["port_edit"].value()
        username = w["user_edit"].text().strip()
        password = w["pass_edit"].text()
        key_path = w["key_edit"].text().strip()

        if not host or not username:
            QMessageBox.warning(self, "Missing info", "Host and username are required.")
            return

        w["connect_btn"].setEnabled(False)
        self.status_bar.showMessage(f"Connecting to {host}...")

        client = self.panes[side]["client"]
        worker = ConnectWorker(client, host, port, username, password, key_path)
        worker.finished.connect(lambda ok, msg, s=side: self._on_connect_finished(s, ok, msg))
        setattr(self, f"_connect_worker_{side}", worker)  # keep a reference alive
        worker.start()

    def _on_connect_finished(self, side, success, message):
        w = self.pane_widgets[side]
        w["connect_btn"].setEnabled(True)
        self.status_bar.showMessage(message)
        if success:
            self._set_pane_controls_enabled(side, True)
            w["root_toggle_btn"].setText("Switch to Root")
            self.panes[side]["path"] = self._safe_home_dir(side)
            self._refresh_remote(side)
        else:
            QMessageBox.critical(self, "Connection failed", message)

    def _safe_home_dir(self, side):
        try:
            return self.panes[side]["client"].get_home_dir()
        except SSHConnectionError:
            return "."

    # ------------------------------------------------------------------ #
    # Root / sudo elevation (per side)
    # ------------------------------------------------------------------ #
    def _on_root_toggle_clicked(self, side):
        client = self.panes[side]["client"]
        enable_root = not client.root_mode
        self._start_root_toggle(side, enable_root, sudo_password=None)

    def _start_root_toggle(self, side, enable_root, sudo_password):
        w = self.pane_widgets[side]
        client = self.panes[side]["client"]
        w["root_toggle_btn"].setEnabled(False)
        self.status_bar.showMessage(
            "Checking sudo access..." if enable_root
            else "Switching back to your normal user..."
        )
        worker = RootToggleWorker(client, enable_root, sudo_password)
        worker.need_password.connect(lambda s=side: self._on_root_password_needed(s))
        worker.finished.connect(
            lambda ok, msg, now_root, s=side: self._on_root_toggle_finished(s, ok, msg, now_root))
        setattr(self, f"_root_worker_{side}", worker)
        worker.start()

    def _on_root_password_needed(self, side):
        client = self.panes[side]["client"]
        password, ok = QInputDialog.getText(
            self, "Sudo password",
            f"sudo password for {client.username}@{client.host}:",
            echo=QLineEdit.Password,
        )
        if ok and password:
            self._start_root_toggle(side, True, password)
        else:
            self.pane_widgets[side]["root_toggle_btn"].setEnabled(True)
            self.status_bar.showMessage("Root switch cancelled")

    def _on_root_toggle_finished(self, side, success, message, now_root):
        w = self.pane_widgets[side]
        w["root_toggle_btn"].setEnabled(True)
        self.status_bar.showMessage(message)
        if success:
            w["root_toggle_btn"].setText("Switch to User" if now_root else "Switch to Root")
            self.panes[side]["path"] = "/" if now_root else self._safe_home_dir(side)
            self._refresh_remote(side)
        else:
            QMessageBox.critical(self, "Root switch failed", message)

    # ------------------------------------------------------------------ #
    # Remote browsing (per side)
    # ------------------------------------------------------------------ #
    def _refresh_remote(self, side):
        client = self.panes[side]["client"]
        path = self.panes[side]["path"]
        w = self.pane_widgets[side]
        try:
            entries = client.list_dir(path)
        except SSHConnectionError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        w["tree"].clear()
        for entry in entries:
            size_str = "" if entry.is_dir else str(entry.size)
            type_str = "Folder" if entry.is_dir else "File"
            item = QTreeWidgetItem([entry.name, size_str, type_str])
            item.setIcon(0, self._folder_icon if entry.is_dir else self._file_icon)
            item.setData(0, Qt.UserRole, entry.is_dir)
            w["tree"].addTopLevelItem(item)
        w["path_label"].setText(f"Remote ({side.upper()}): {path}")

    def _remote_go_up(self, side):
        path = self.panes[side]["path"]
        if path in (".", "/"):
            return
        self.panes[side]["path"] = os.path.dirname(path.rstrip("/")) or "/"
        self._refresh_remote(side)

    def _on_remote_double_click(self, side, item, _column):
        is_dir = item.data(0, Qt.UserRole)
        if is_dir:
            path = self.panes[side]["path"]
            self.panes[side]["path"] = path.rstrip("/") + "/" + item.text(0)
            self._refresh_remote(side)

    def _on_mkdir(self, side):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            path = self.panes[side]["path"]
            try:
                self.panes[side]["client"].mkdir(path.rstrip("/") + "/" + name)
                self._refresh_remote(side)
            except SSHConnectionError as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _on_delete_remote(self, side):
        w = self.pane_widgets[side]
        item = w["tree"].currentItem()
        if not item:
            return
        is_dir = item.data(0, Qt.UserRole)
        confirm = QMessageBox.question(self, "Confirm delete", f"Delete '{item.text(0)}'?")
        if confirm == QMessageBox.Yes:
            try:
                path = self.panes[side]["path"]
                full_path = path.rstrip("/") + "/" + item.text(0)
                self.panes[side]["client"].remove(full_path, is_dir)
                self._refresh_remote(side)
            except SSHConnectionError as exc:
                QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------ #
    # Transfers
    # ------------------------------------------------------------------ #
    def _on_transfer_left_to_right(self):
        """Left -> Right. In local mode: local file -> server B.
        In server mode: server A -> server B."""
        if self.mode == "local":
            index = self.local_view.currentIndex()
            if not index.isValid():
                QMessageBox.information(self, "Select a file", "Pick a local file or folder first.")
                return
            local_path = self.local_model.filePath(index)
            is_dir = self.local_model.isDir(index)
            dst_dir = self.panes["b"]["path"]
            dst_path = dst_dir.rstrip("/") + "/" + os.path.basename(local_path)
            self._pending_transfer_side = "b"
            self._start_local_transfer("upload", self.panes["b"]["client"],
                                        local_path, dst_path, is_dir)
        else:
            item = self.pane_widgets["a"]["tree"].currentItem()
            if not item:
                QMessageBox.information(self, "Select a file", "Pick a file or folder on server A first.")
                return
            is_dir = item.data(0, Qt.UserRole)
            src_path = self.panes["a"]["path"].rstrip("/") + "/" + item.text(0)
            dst_dir = self.panes["b"]["path"]
            dst_path = dst_dir.rstrip("/") + "/" + item.text(0)
            self._pending_transfer_side = "b"
            self._start_relay_transfer(self.panes["a"]["client"], self.panes["b"]["client"],
                                        src_path, dst_path, is_dir)

    def _on_transfer_right_to_left(self):
        """Right -> Left. In local mode: server B -> local disk (with a
        destination picker). In server mode: server B -> server A."""
        item = self.pane_widgets["b"]["tree"].currentItem()
        if not item:
            QMessageBox.information(self, "Select a file", "Pick a file or folder on the server first.")
            return
        is_dir = item.data(0, Qt.UserRole)
        src_path = self.panes["b"]["path"].rstrip("/") + "/" + item.text(0)

        if self.mode == "local":
            local_dir = QFileDialog.getExistingDirectory(self, "Choose download destination",
                                                           os.path.expanduser("~"))
            if not local_dir:
                return
            local_target = os.path.join(local_dir, item.text(0))
            self._pending_transfer_side = None
            self._start_local_transfer("download", self.panes["b"]["client"],
                                        src_path, local_target, is_dir)
        else:
            dst_dir = self.panes["a"]["path"]
            dst_path = dst_dir.rstrip("/") + "/" + item.text(0)
            self._pending_transfer_side = "a"
            self._start_relay_transfer(self.panes["b"]["client"], self.panes["a"]["client"],
                                        src_path, dst_path, is_dir)

    def _start_local_transfer(self, action, client, src, dst, is_dir):
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(f"{action.capitalize()}ing {os.path.basename(src)}...")
        worker = TransferWorker(action, client, src, dst, is_dir)
        worker.progress.connect(self._on_transfer_progress)
        worker.finished.connect(self._on_transfer_finished)
        self._transfer_worker = worker
        worker.start()

    def _start_relay_transfer(self, client_from, client_to, src, dst, is_dir):
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(f"Transferring {os.path.basename(src)} between servers...")
        worker = RelayTransferWorker(client_from, client_to, src, dst, is_dir)
        worker.progress.connect(self._on_transfer_progress)
        worker.finished.connect(self._on_transfer_finished)
        self._transfer_worker = worker
        worker.start()

    def _on_transfer_progress(self, done, total):
        if total:
            self.progress_bar.setValue(int(done * 100 / total))

    def _on_transfer_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(message)
        if success and self._pending_transfer_side:
            self._refresh_remote(self._pending_transfer_side)
        elif not success:
            QMessageBox.critical(self, "Transfer failed", message)
        self._pending_transfer_side = None

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        for side in ("a", "b"):
            self.panes[side]["client"].disconnect()
        event.accept()