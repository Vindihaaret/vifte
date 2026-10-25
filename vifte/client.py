"""Klient for socket-protokollen til vifted."""
import json
import os
import socket

from .daemon import SYSTEM_SOCKET, user_paths


def default_socket():
    if os.environ.get("VIFTE_SOCKET"):
        return os.environ["VIFTE_SOCKET"]
    user = user_paths()[1]
    if not os.path.exists(SYSTEM_SOCKET) and os.path.exists(user):
        return user
    return SYSTEM_SOCKET


class DaemonError(Exception):
    pass


class Client:
    def __init__(self, path=None):
        self.path = path or default_socket()
        self.sock = None
        self.file = None

    def connect(self):
        self.close()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.path)
        self.sock, self.file = s, s.makefile("rwb")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = self.file = None

    def call(self, cmd, **kw):
        for attempt in (0, 1):
            try:
                if self.sock is None:
                    self.connect()
                self.file.write((json.dumps({"cmd": cmd, **kw}) + "\n").encode())
                self.file.flush()
                line = self.file.readline()
                if not line:
                    raise ConnectionError("tjenesten lukket forbindelsen")
                resp = json.loads(line)
                break
            except (OSError, ConnectionError):
                self.close()
                if attempt:
                    raise
        if not resp.pop("ok", False):
            raise DaemonError(resp.get("error", "ukjent feil"))
        return resp
