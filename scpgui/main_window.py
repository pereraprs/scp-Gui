"""
main_window.py
---------------
PyQt5 GUI: a dual-pane file manager (local | remote) for SCP/SFTP transfers,
similar in spirit to WinSCP / FileZilla.

The remote pane is a real hierarchical tree (same idea as the local
QFileSystemModel/QTreeView pane): folders show an expand arrow, expanding
one lazily fetches its children over SFTP, and collapsing/re-expanding
never loses your place -- unlike a single "current directory" view where
navigating into a folder discards the parent listing.
"""

import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QPushButton, QLabel, QSplitter, QTreeView,
    QTreeWidget, QTreeWidgetItem, QFileSystemModel, QToolBar, QAction,
    QProgressBar, QMessageBox, QInputDialog, QFileDialog, QStatusBar,
    QComboBox, QApplication,
)

from scpgui.ssh_client import SCPClient, SSHConnectionError
from scpgui import connections
from scpgui import theme

# Extra data roles stored on each remote QTreeWidgetItem, alongside the
# existing Qt.UserRole (used for is_dir).
PATH_ROLE = Qt.UserRole + 1
LOADED_ROLE = Qt.UserRole + 2


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


# ---------------------------------------------------------------------- #
# Main window
# ---------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SCP GUI — Secure File Transfer")
        self.resize(1100, 650)

        self.client = SCPClient()
        self.dark_mode = True
        self._root_item = None            # top-level remote tree item (home dir)
        self._pending_transfer_dir = None  # directory item to refresh after a transfer

        self._build_ui()
        self._reload_profiles()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

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

        # remote pane -- a real expandable tree, same idea as the local one
        remote_container = QWidget()
        remote_layout = QVBoxLayout(remote_container)
        self.remote_path_label = QLabel("Remote: (not connected)")
        remote_layout.addWidget(self.remote_path_label)
        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(["Name", "Size", "Type"])
        self.remote_tree.setColumnWidth(0, 220)
        self.remote_tree.itemExpanded.connect(self._on_item_expanded)
        self.remote_tree.currentItemChanged.connect(self._on_remote_selection_changed)
        remote_layout.addWidget(self.remote_tree)
        remote_btns = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._on_refresh_clicked)
        mkdir_btn = QPushButton("New Folder")
        mkdir_btn.clicked.connect(self._on_mkdir)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._on_delete_remote)
        for b in (refresh_btn, mkdir_btn, delete_btn):
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
            try:
                home_dir = self.client.get_home_dir()
            except SSHConnectionError:
                home_dir = "."
            self._populate_root(home_dir)
        else:
            QMessageBox.critical(self, "Connection failed", message)

    # ------------------------------------------------------------------ #
    # Remote browsing -- hierarchical tree, lazily loaded per folder
    # ------------------------------------------------------------------ #
    def _populate_root(self, home_dir):
        self.remote_tree.clear()
        root = QTreeWidgetItem([home_dir, "", "Folder"])
        root.setData(0, Qt.UserRole, True)      # is_dir
        root.setData(0, PATH_ROLE, home_dir)
        root.setData(0, LOADED_ROLE, False)
        self.remote_tree.addTopLevelItem(root)
        self._root_item = root
        self._add_dummy_child(root)
        self.remote_tree.expandItem(root)  # triggers _on_item_expanded to load real children
        self.remote_path_label.setText(f"Remote: {home_dir}")

    @staticmethod
    def _add_dummy_child(item):
        placeholder = QTreeWidgetItem(["Loading...", "", ""])
        placeholder.setData(0, Qt.UserRole, False)
        item.addChild(placeholder)

    def _load_children(self, item):
        """Fetch this directory's children over SFTP and populate the item."""
        path = item.data(0, PATH_ROLE)
        try:
            entries = self.client.list_dir(path)
        except SSHConnectionError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        item.takeChildren()  # drop the "Loading..." placeholder (or stale children)
        for entry in entries:
            size_str = "" if entry.is_dir else str(entry.size)
            type_str = "Folder" if entry.is_dir else "File"
            child = QTreeWidgetItem([entry.name, size_str, type_str])
            child.setData(0, Qt.UserRole, entry.is_dir)
            child.setData(0, PATH_ROLE, path.rstrip("/") + "/" + entry.name)
            child.setData(0, LOADED_ROLE, False)
            if entry.is_dir:
                self._add_dummy_child(child)
            item.addChild(child)
        item.setData(0, LOADED_ROLE, True)

    def _on_item_expanded(self, item):
        if not item.data(0, LOADED_ROLE):
            self._load_children(item)

    def _reload_item(self, item):
        """Force-refresh a directory node's children (e.g. after a change)."""
        if item is None:
            return
        item.setData(0, LOADED_ROLE, False)
        if item.isExpanded():
            self._load_children(item)
        else:
            item.takeChildren()
            self._add_dummy_child(item)

    def _on_refresh_clicked(self):
        item = self.remote_tree.currentItem()
        if item is not None:
            target = item if item.data(0, Qt.UserRole) else (item.parent() or self._root_item)
            self._reload_item(target)
        elif self._root_item is not None:
            self._reload_item(self._root_item)

    def _on_remote_selection_changed(self, current, _previous):
        if current is None:
            return
        path = current.data(0, PATH_ROLE)
        is_dir = current.data(0, Qt.UserRole)
        target = path if is_dir else self._parent_path(current)
        self.remote_path_label.setText(f"Remote: {target}")

    def _parent_path(self, item):
        parent = item.parent()
        if parent is not None:
            return parent.data(0, PATH_ROLE)
        return self._root_item.data(0, PATH_ROLE) if self._root_item else "/"

    def _current_remote_dir_item(self):
        """The directory item that uploads / new folders should target."""
        item = self.remote_tree.currentItem()
        if item is None:
            return self._root_item
        if item.data(0, Qt.UserRole):
            return item
        return item.parent() or self._root_item

    def _on_mkdir(self):
        dir_item = self._current_remote_dir_item()
        if dir_item is None:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if ok and name:
            dir_path = dir_item.data(0, PATH_ROLE)
            try:
                self.client.mkdir(dir_path.rstrip("/") + "/" + name)
                self._reload_item(dir_item)
            except SSHConnectionError as exc:
                QMessageBox.critical(self, "Error", str(exc))

    def _on_delete_remote(self):
        item = self.remote_tree.currentItem()
        if not item or item is self._root_item:
            return
        is_dir = item.data(0, Qt.UserRole)
        full_path = item.data(0, PATH_ROLE)
        confirm = QMessageBox.question(
            self, "Confirm delete", f"Delete '{item.text(0)}'?",
        )
        if confirm == QMessageBox.Yes:
            try:
                self.client.remove(full_path, is_dir)
                self._reload_item(item.parent() or self._root_item)
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
        dir_item = self._current_remote_dir_item()
        if dir_item is None:
            QMessageBox.information(self, "Not connected", "Connect to a remote host first.")
            return

        local_path = self.local_model.filePath(index)
        is_dir = self.local_model.isDir(index)
        remote_dir = dir_item.data(0, PATH_ROLE)
        remote_target = remote_dir.rstrip("/") + "/" + os.path.basename(local_path)
        self._pending_transfer_dir = dir_item
        self._start_transfer("upload", local_path, remote_target, is_dir)

    def _on_download(self):
        item = self.remote_tree.currentItem()
        if not item or item is self._root_item:
            QMessageBox.information(self, "Select a file", "Pick a remote file or folder first.")
            return
        is_dir = item.data(0, Qt.UserRole)
        remote_path = item.data(0, PATH_ROLE)

        local_dir = QFileDialog.getExistingDirectory(self, "Choose download destination",
                                                       os.path.expanduser("~"))
        if not local_dir:
            return
        local_target = os.path.join(local_dir, item.text(0))
        self._pending_transfer_dir = None
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
        if success and self._pending_transfer_dir is not None:
            self._reload_item(self._pending_transfer_dir)
        elif not success:
            QMessageBox.critical(self, "Transfer failed", message)
        self._pending_transfer_dir = None

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.client.disconnect()
        event.accept()