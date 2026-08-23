"""
ssh_client.py
-------------
Backend wrapper around paramiko that provides the secure-copy (SCP/SFTP)
operations used by the GUI: connecting, listing directories, uploading,
downloading, creating folders, deleting, and renaming.

We use SFTP (a sub-protocol carried over the same SSH connection) rather
than the legacy SCP protocol because SFTP supports directory listing,
resuming, and richer error reporting -- which is what a file-manager-style
app needs. Functionally, from the user's point of view, this is still
"secure copy over SSH".
"""

import os
import stat
import socket
import paramiko


class SSHConnectionError(Exception):
    """Raised when a connection or authentication attempt fails."""
    pass


class RemoteEntry:
    """Represents one file/directory entry on the remote host."""

    def __init__(self, name, is_dir, size, mtime):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.mtime = mtime


class SCPClient:
    """High-level client used by the GUI layer.

    Usage:
        client = SCPClient()
        client.connect(host, port, username, password=..., key_path=...)
        entries = client.list_dir("/remote/path")
        client.download("/remote/path/file.txt", "/local/path/file.txt")
        client.upload("/local/path/file.txt", "/remote/path/file.txt")
        client.disconnect()
    """

    def __init__(self):
        self._ssh = None
        self._sftp = None
        self.host = None
        self.username = None
        self.connected = False
        self.root_mode = False

    # ------------------------------------------------------------------ #
    # Connection management
    # ------------------------------------------------------------------ #
    def connect(self, host, port, username, password=None, key_path=None,
                timeout=10):
        """Open an SSH connection and an SFTP session on top of it.

        Either `password` or `key_path` (path to a private key file) must
        be supplied. Raises SSHConnectionError on any failure.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if key_path:
                pkey = self._load_private_key(key_path, password)
                client.connect(
                    hostname=host, port=port, username=username,
                    pkey=pkey, timeout=timeout,
                )
            else:
                client.connect(
                    hostname=host, port=port, username=username,
                    password=password, timeout=timeout,
                )
        except paramiko.AuthenticationException as exc:
            raise SSHConnectionError(f"Authentication failed: {exc}")
        except (paramiko.SSHException, socket.error, OSError) as exc:
            raise SSHConnectionError(f"Could not connect to {host}: {exc}")

        self._ssh = client
        self._sftp = client.open_sftp()
        self.host = host
        self.username = username
        self.connected = True
        self.root_mode = False

    @staticmethod
    def _load_private_key(key_path, passphrase):
        """Try the common key types until one parses the given file.

        DSSKey (DSA) was removed in paramiko 4.0+ since DSA keys are
        deprecated/insecure and no longer supported by OpenSSH either,
        so we only reference it if the installed paramiko still has it.
        """
        key_classes = [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]
        if hasattr(paramiko, "DSSKey"):
            key_classes.append(paramiko.DSSKey)
        last_error = None
        for cls in key_classes:
            try:
                return cls.from_private_key_file(key_path, password=passphrase)
            except paramiko.SSHException as exc:
                last_error = exc
                continue
        raise SSHConnectionError(f"Unable to load private key: {last_error}")

    def disconnect(self):
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._ssh:
            self._ssh.close()
            self._ssh = None
        self.connected = False
        self.root_mode = False

    def _require_connection(self):
        if not self.connected or self._sftp is None:
            raise SSHConnectionError("Not connected to a remote host.")

    def get_home_dir(self):
        """Return the absolute path of the remote login directory.

        SFTP servers accept "." as shorthand for the current (home)
        directory, but that's confusing to display in the GUI -- this
        resolves it to a real path like /home/ubuntu.
        """
        self._require_connection()
        return self._sftp.normalize(".")

    # ------------------------------------------------------------------ #
    # Root / sudo elevation
    # ------------------------------------------------------------------ #
    # Plain SFTP has no notion of privilege escalation -- it just runs
    # with whatever Unix permissions the logged-in user already has.
    # These methods work around that by asking the remote shell to run
    # the sftp-server binary through `sudo` instead of going through the
    # standard "sftp" subsystem, then wrapping that channel as a normal
    # SFTPClient. This requires the logged-in account to have sudo
    # rights on the VM (which is the default for the "ubuntu" user on
    # most cloud/VM images).

    def check_passwordless_sudo(self):
        """True if `sudo` needs no password for this account right now."""
        self._require_connection()
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        try:
            channel.settimeout(10)
            channel.exec_command("sudo -n true")
            return channel.recv_exit_status() == 0
        except Exception:
            return False  # can't confirm passwordless -> fall back to prompting
        finally:
            channel.close()

    def elevate_to_root(self, sudo_password=None,
                         sftp_server_path="/usr/lib/openssh/sftp-server"):
        """Swap the active SFTP session for one running as root via sudo.

        Pass sudo_password=None when the account has passwordless sudo
        (check with check_passwordless_sudo() first) -- sending a
        password sudo never asked for would otherwise leak into the
        SFTP binary stream right after it and break the protocol.
        """
        self._require_connection()
        transport = self._ssh.get_transport()
        channel = transport.open_session()
        channel.settimeout(15)

        try:
            channel.exec_command(f"sudo -S -p '' {sftp_server_path}")
            if sudo_password:
                channel.send((sudo_password + "\n").encode())
            new_sftp = paramiko.SFTPClient(channel)
        except Exception as exc:
            channel.close()
            raise SSHConnectionError(
                f"Could not start a root session via sudo: {exc}. "
                "Check the sudo password, or that this account has "
                "sudo rights on the VM."
            )

        self._sftp.close()
        self._sftp = new_sftp
        self.root_mode = True

    def drop_to_user(self):
        """Swap back from a root (sudo) SFTP session to the normal one."""
        if self._ssh is None:
            raise SSHConnectionError("Not connected.")
        new_sftp = self._ssh.open_sftp()
        if self._sftp:
            self._sftp.close()
        self._sftp = new_sftp
        self.root_mode = False

    # ------------------------------------------------------------------ #
    # Directory / file operations
    # ------------------------------------------------------------------ #
    def list_dir(self, remote_path):
        """Return a sorted list of RemoteEntry objects for remote_path."""
        self._require_connection()
        entries = []
        for attr in self._sftp.listdir_attr(remote_path):
            is_dir = stat.S_ISDIR(attr.st_mode)
            entries.append(RemoteEntry(attr.filename, is_dir, attr.st_size,
                                        attr.st_mtime))
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def mkdir(self, remote_path):
        self._require_connection()
        self._sftp.mkdir(remote_path)

    def remove(self, remote_path, is_dir):
        self._require_connection()
        if is_dir:
            self._rmdir_recursive(remote_path)
        else:
            self._sftp.remove(remote_path)

    def _rmdir_recursive(self, remote_path):
        for entry in self.list_dir(remote_path):
            full = remote_path.rstrip("/") + "/" + entry.name
            if entry.is_dir:
                self._rmdir_recursive(full)
            else:
                self._sftp.remove(full)
        self._sftp.rmdir(remote_path)

    def rename(self, remote_old, remote_new):
        self._require_connection()
        self._sftp.rename(remote_old, remote_new)

    # ------------------------------------------------------------------ #
    # Transfers
    # ------------------------------------------------------------------ #
    def upload(self, local_path, remote_path, progress_callback=None):
        """Upload a single local file to remote_path.

        progress_callback(bytes_transferred, total_bytes) is called
        periodically by paramiko if provided.
        """
        self._require_connection()
        self._sftp.put(local_path, remote_path, callback=progress_callback)

    def download(self, remote_path, local_path, progress_callback=None):
        """Download a single remote file to local_path."""
        self._require_connection()
        self._sftp.get(remote_path, local_path, callback=progress_callback)

    def upload_directory(self, local_dir, remote_dir, progress_callback=None):
        """Recursively upload a local directory tree."""
        self._require_connection()
        try:
            self._sftp.mkdir(remote_dir)
        except IOError:
            pass  # already exists

        for item in os.listdir(local_dir):
            local_item = os.path.join(local_dir, item)
            remote_item = remote_dir.rstrip("/") + "/" + item
            if os.path.isdir(local_item):
                self.upload_directory(local_item, remote_item, progress_callback)
            else:
                self.upload(local_item, remote_item, progress_callback)

    def download_directory(self, remote_dir, local_dir, progress_callback=None):
        """Recursively download a remote directory tree."""
        self._require_connection()
        os.makedirs(local_dir, exist_ok=True)

        for entry in self.list_dir(remote_dir):
            remote_item = remote_dir.rstrip("/") + "/" + entry.name
            local_item = os.path.join(local_dir, entry.name)
            if entry.is_dir:
                self.download_directory(remote_item, local_item, progress_callback)
            else:
                self.download(remote_item, local_item, progress_callback)