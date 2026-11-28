"""Per-user application lock and window activation channel."""
import ctypes
import hashlib
import sys
from time import monotonic, sleep

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance:
    def __init__(self, directory):
        identity = str(directory.resolve()).casefold() if sys.platform == "win32" else str(directory.resolve())
        self.name = "Pivluma-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.lock = QLockFile(str(directory / "instance.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def acquire(self, replacing=False):
        deadline = monotonic() + (20 if replacing else 3)
        while monotonic() < deadline:
            if self.lock.tryLock(0):
                QLocalServer.removeServer(self.name)
                if self.server.listen(self.name):
                    return True
                self.lock.unlock()
                return False
            if not replacing and self.notify_existing():
                return False
            sleep(.05)
        return False

    def notify_existing(self):
        if sys.platform == "win32":
            pid, _, _ = self.lock.getLockInfo()
            if pid > 0:
                ctypes.windll.user32.AllowSetForegroundWindow(pid)
        client = QLocalSocket()
        client.connectToServer(self.name)
        if not client.waitForConnected(200):
            return False
        client.disconnectFromServer()
        return True

    def activate_on_connection(self, activate):
        def deliver():
            while self.server.hasPendingConnections():
                socket = self.server.nextPendingConnection()
                socket.deleteLater()
            activate()
        self.server.newConnection.connect(deliver)

    def release(self):
        self.server.close()
        self.lock.unlock()
