"""
main_window.py
---------------
PyQt5 GUI: a dual-pane file manager (local | remote) for SCP/SFTP transfers,
similar in spirit to WinSCP / FileZilla.

The remote pane shows one directory at a time (like the local pane's
current folder), with an "Up" button to go to the parent directory and
double-click to enter a folder. Folders and files get distinct icons on
both sides, same as a normal desktop file manager.
"""

import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QPushButton, QLabel, QSplitter, QTreeView,
    QTreeWidget, QTreeWidgetItem, QFileSystemModel, QToolBar, QAction,
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


class RootToggleWorker(QThread):
    """Switches the active SFTP session between the normal login user
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
        self.resize(1100, 650)

        self.client = SCPClient()
        self.remote_path = "/"
        self.dark_mode = True

        self._build_ui()
        self._reload_profiles()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # Standard folder/file icons -- same idea as the local pane, which
        # gets these automatically from QFileSystemModel.
        style = self.style()
        self._folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        self._file_icon = style.standardIcon(QStyle.SP_FileIcon)

        # --- saved connections bar ---
        # Lets you jump between links like "PC -> Build VM" or
        # "VM1 -> VM2" without retyping host/user/auth each time.
        profiles_bar = QHBoxLayout()
        profiles_bar.addWidget(QLabel("Saved:"))
        self.profiles_combo = QComboBox()
        self.profiles_combo.setMinimumWidth(220)
        self.profiles_combo.currentIndexChanged.connect(self._on_profile_selected)
        profiles_bar.addWidget(self.profiles_combo)
        save_profile_btn = QPushButton("Save As...")
        save_profile_btn.clicked.connect(self._on_save_profile)
        profiles_bar.addWidget(save_profile_btn)
        delete_profile_btn = QPushButton("Delete")
        delete_profile_btn.clicked.connect(self._on_delete_profile)
        profiles_bar.addWidget(delete_profile_btn)
        profiles_bar.addStretch()
        self.theme_btn = QPushButton("☀ Light mode")
        self.theme_btn.clicked.connect(self._toggle_theme)
        profiles_bar.addWidget(self.theme_btn)
        outer.addLayout(profiles_bar)

        # --- connection bar ---
        conn_bar = QHBoxLayout()
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("host")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(22)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("username")
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("password (or leave blank for key)")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("private key path (optional)")
        key_browse = QPushButton("...")
        key_browse.setMaximumWidth(30)
        key_browse.clicked.connect(self._browse_key)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        for w, label in [
            (self.host_edit, "Host"), (self.port_edit, "Port"),
            (self.user_edit, "User"), (self.pass_edit, "Password"),
        ]:
            conn_bar.addWidget(QLabel(label + ":"))
            conn_bar.addWidget(w)
        conn_bar.addWidget(QLabel("Key:"))
        conn_bar.addWidget(self.key_edit)
        conn_bar.addWidget(key_browse)
        conn_bar.addWidget(self.connect_btn)
        outer.addLayout(conn_bar)

        # --- dual pane splitter ---
        splitter = QSplitter(Qt.Horizontal)

        # local pane
        local_container = QWidget()
        local_layout = QVBoxLayout(local_container)
        local_layout.addWidget(QLabel("Local"))
        self.local_model = QFileSystemModel()
        self.local_model.setRootPath(os.path.expanduser("~"))
        self.local_view = QTreeView()
        self.local_view.setModel(self.local_model)
        self.local_view.setRootIndex(self.local_model.index(os.path.expanduser("~")))
        self.local_view.setColumnWidth(0, 250)
        local_layout.addWidget(self.local_view)
        splitter.addWidget(local_container)

        # transfer buttons (middle)
        mid_container = QWidget()
        mid_layout = QVBoxLayout(mid_container)
        mid_layout.addStretch()
        self.upload_btn = QPushButton("Upload →")
        self.upload_btn.clicked.connect(self._on_upload)
        self.download_btn = QPushButton("← Download")
        self.download_btn.clicked.connect(self._on_download)
        mid_layout.addWidget(self.upload_btn)
        mid_layout.addWidget(self.download_btn)
        mid_layout.addStretch()
        splitter.addWidget(mid_container)

        # remote pane -- current directory + Up button, same shape as before
        remote_container = QWidget()
        remote_layout = QVBoxLayout(remote_container)
        self.remote_path_label = QLabel("Remote: (not connected)")
        remote_layout.addWidget(self.remote_path_label)
        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(["Name", "Size", "Type"])
        self.remote_tree.setColumnWidth(0, 220)
        self.remote_tree.itemDoubleClicked.connect(self._on_remote_double_click)
        remote_layout.addWidget(self.remote_tree)
        remote_btns = QHBoxLayout()
        up_btn = QPushButton("Up")
        up_btn.clicked.connect(self._remote_go_up)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_remote)
        mkdir_btn = QPushButton("New Folder")
        mkdir_btn.clicked.connect(self._on_mkdir)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._on_delete_remote)
        self.root_toggle_btn = QPushButton("Switch to Root")
        self.root_toggle_btn.setToolTip(
            "Browse and modify the whole VM filesystem via sudo, not "
            "just what your login user can see. Needs sudo rights on "
            "the remote account."
        )
        self.root_toggle_btn.clicked.connect(self._on_root_toggle_clicked)
        for b in (up_btn, refresh_btn, mkdir_btn, delete_btn, self.root_toggle_btn):
            remote_btns.addWidget(b)
        remote_layout.addLayout(remote_btns)
        splitter.addWidget(remote_container)

        splitter.setSizes([400, 80, 400])
        outer.addWidget(splitter, stretch=1)

        # --- progress + status ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        outer.addWidget(self.progress_bar)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Not connected")

        self._set_remote_controls_enabled(False)

    def _set_remote_controls_enabled(self, enabled):
        self.upload_btn.setEnabled(enabled)
        self.download_btn.setEnabled(enabled)
        self.remote_tree.setEnabled(enabled)
        self.root_toggle_btn.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # Saved connection profiles
    # ------------------------------------------------------------------ #
    def _reload_profiles(self):
        self._profiles = connections.load_profiles()
        self.profiles_combo.blockSignals(True)
        self.profiles_combo.clear()
        self.profiles_combo.addItem("-- select a saved connection --")
        for p in self._profiles:
            self.profiles_combo.addItem(p.name)
        self.profiles_combo.blockSignals(False)

    def _on_profile_selected(self, index):
        if index <= 0:
            return
        profile = self._profiles[index - 1]
        self.host_edit.setText(profile.host)
        self.port_edit.setValue(profile.port)
        self.user_edit.setText(profile.username)
        self.pass_edit.clear()  # never stored -- type it if this profile uses a password
        self.key_edit.setText(profile.key_path if profile.auth_type == "key" else "")

    def _on_save_profile(self):
        host = self.host_edit.text().strip()
        username = self.user_edit.text().strip()
        if not host or not username:
            QMessageBox.warning(self, "Missing info", "Fill in host and username first.")
            return

        default_name = f"{username}@{host}"
        name, ok = QInputDialog.getText(self, "Save connection", "Name:",
                                         text=default_name)
        if not (ok and name):
            return

        key_path = self.key_edit.text().strip()
        auth_type = "key" if key_path else "password"
        profile = connections.ConnectionProfile(
            name=name, host=host, port=self.port_edit.value(),
            username=username, auth_type=auth_type, key_path=key_path,
        )
        connections.upsert_profile(profile)
        self._reload_profiles()
        self.profiles_combo.setCurrentText(name)
        self.status_bar.showMessage(f"Saved connection '{name}'")

    def _on_delete_profile(self):
        index = self.profiles_combo.currentIndex()
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
        self.dark_mode = not self.dark_mode
        if self.dark_mode:
            theme.apply_dark_theme(app)
            self.theme_btn.setText("☀ Light mode")
        else:
            theme.apply_light_theme(app)
            self.theme_btn.setText("🌙 Dark mode")

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #
    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select private key",
                                               os.path.expanduser("~/.ssh"))
        if path:
            self.key_edit.setText(path)

    def _on_connect_clicked(self):
        host = self.host_edit.text().strip()
        port = self.port_edit.value()
        username = self.user_edit.text().strip()
        password = self.pass_edit.text()
        key_path = self.key_edit.text().strip()

        if not host or not username:
            QMessageBox.warning(self, "Missing info", "Host and username are required.")
            return

        self.connect_btn.setEnabled(False)
        self.status_bar.showMessage(f"Connecting to {host}...")

        self._connect_worker = ConnectWorker(self.client, host, port, username,
                                              password, key_path)
        self._connect_worker.finished.connect(self._on_connect_finished)
        self._connect_worker.start()

    def _on_connect_finished(self, success, message):
        self.connect_btn.setEnabled(True)
        self.status_bar.showMessage(message)
        if success:
            self._set_remote_controls_enabled(True)
            self.root_toggle_btn.setText("Switch to Root")  # fresh connection always starts as the login user
            self.remote_path = self._safe_home_dir()
            self._refresh_remote()
        else:
            QMessageBox.critical(self, "Connection failed", message)

    def _safe_home_dir(self):
        try:
            return self.client.get_home_dir()
        except SSHConnectionError:
            return "."

    # ------------------------------------------------------------------ #
    # Root / sudo elevation
    # ------------------------------------------------------------------ #
    def _on_root_toggle_clicked(self):
        enable_root = not self.client.root_mode
        self._start_root_toggle(enable_root, sudo_password=None)

    def _start_root_toggle(self, enable_root, sudo_password):
        self.root_toggle_btn.setEnabled(False)
        self.status_bar.showMessage(
            "Checking sudo access..." if enable_root
            else "Switching back to your normal user..."
        )
        self._root_worker = RootToggleWorker(self.client, enable_root, sudo_password)
        self._root_worker.need_password.connect(self._on_root_password_needed)
        self._root_worker.finished.connect(self._on_root_toggle_finished)
        self._root_worker.start()

    def _on_root_password_needed(self):
        password, ok = QInputDialog.getText(
            self, "Sudo password",
            f"sudo password for {self.client.username}@{self.client.host}:",
            echo=QLineEdit.Password,
        )
        if ok and password:
            self._start_root_toggle(True, password)
        else:
            self.root_toggle_btn.setEnabled(True)
            self.status_bar.showMessage("Root switch cancelled")

    def _on_root_toggle_finished(self, success, message, now_root):
        self.root_toggle_btn.setEnabled(True)
        self.status_bar.showMessage(message)
        if success:
            self.root_toggle_btn.setText("Switch to User" if now_root else "Switch to Root")
            self.remote_path = "/" if now_root else self._safe_home_dir()
            self._refresh_remote()
        else:
            QMessageBox.critical(self, "Root switch failed", message)

    # ------------------------------------------------------------------ #
    # Remote browsing
    # ------------------------------------------------------------------ #
    def _refresh_remote(self):
        try:
            entries = self.client.list_dir(self.remote_path)
        except SSHConnectionError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        self.remote_tree.clear()
        for entry in entries:
            size_str = "" if entry.is_dir else str(entry.size)
            type_str = "Folder" if entry.is_dir else "File"
            item = QTreeWidgetItem([entry.name, size_str, type_str])
            item.setIcon(0, self._folder_icon if entry.is_dir else self._file_icon)
            item.setData(0, Qt.UserRole, entry.is_dir)
            self.remote_tree.addTopLevelItem(item)
        self.remote_path_label.setText(f"Remote: {self.remote_path}")

    def _remote_go_up(self):
        if self.remote_path in (".", "/"):
            return
        self.remote_path = os.path.dirname(self.remote_path.rstrip("/")) or "/"
        self._refresh_remote()

    def _on_remote_double_click(self, item, _column):
        is_dir = item.data(0, Qt.UserRole)
        if is_dir:
            self.remote_path = self.remote_path.rstrip("/") + "/" + item.text(0)
            self._refresh_remote()

    def _on_mkdir(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            try:
                self.client.mkdir(self.remote_path.rstrip("/") + "/" + name)
                self._refresh_remote()
            except SSHConnectionError as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _on_delete_remote(self):
        item = self.remote_tree.currentItem()
        if not item:
            return
        is_dir = item.data(0, Qt.UserRole)
        confirm = QMessageBox.question(
            self, "Confirm delete", f"Delete '{item.text(0)}'?",
        )
        if confirm == QMessageBox.Yes:
            try:
                full_path = self.remote_path.rstrip("/") + "/" + item.text(0)
                self.client.remove(full_path, is_dir)
                self._refresh_remote()
            except SSHConnectionError as exc:
                QMessageBox.critical(self, "Error", str(exc))

    # ------------------------------------------------------------------ #
    # Transfers
    # ------------------------------------------------------------------ #
    def _on_upload(self):
        index = self.local_view.currentIndex()
        if not index.isValid():
            QMessageBox.information(self, "Select a file", "Pick a local file or folder first.")
            return
        local_path = self.local_model.filePath(index)
        is_dir = self.local_model.isDir(index)
        remote_target = self.remote_path.rstrip("/") + "/" + os.path.basename(local_path)
        self._start_transfer("upload", local_path, remote_target, is_dir)

    def _on_download(self):
        item = self.remote_tree.currentItem()
        if not item:
            QMessageBox.information(self, "Select a file", "Pick a remote file or folder first.")
            return
        is_dir = item.data(0, Qt.UserRole)
        remote_path = self.remote_path.rstrip("/") + "/" + item.text(0)

        local_dir = QFileDialog.getExistingDirectory(self, "Choose download destination",
                                                       os.path.expanduser("~"))
        if not local_dir:
            return
        local_target = os.path.join(local_dir, item.text(0))
        self._start_transfer("download", remote_path, local_target, is_dir)

    def _start_transfer(self, action, src, dst, is_dir):
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(f"{action.capitalize()}ing {os.path.basename(src)}...")

        self._transfer_worker = TransferWorker(action, self.client, src, dst, is_dir)
        self._transfer_worker.progress.connect(self._on_transfer_progress)
        self._transfer_worker.finished.connect(self._on_transfer_finished)
        self._transfer_worker.start()

    def _on_transfer_progress(self, done, total):
        if total:
            self.progress_bar.setValue(int(done * 100 / total))

    def _on_transfer_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(message)
        if success:
            self._refresh_remote()
        else:
            QMessageBox.critical(self, "Transfer failed", message)

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.client.disconnect()
        event.accept()