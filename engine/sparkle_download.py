"""Serve one authenticated loopback request from Sparkle's native download task."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import socket
import sys
from urllib.parse import urlsplit

from ota_transfer import download


def serve(source, size, token, parent):
    path = "/" + token + "/" + urlsplit(source).path.rsplit("/", 1)[-1]

    class Archive(BaseHTTPRequestHandler):
        timeout = 15

        def do_GET(self):
            if self.path != path:
                self.send_error(404)
                return
            self.server.served = True
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            download(source, size, self.wfile.write, checkpoint=check_parent)

        def log_message(self, *_):
            pass

    def check_parent():
        if os.getppid() != parent:
            raise InterruptedError("Updater exited")

    with HTTPServer(("127.0.0.1", 0), Archive, bind_and_activate=False) as server:
        server.socket.close()
        server.socket = socket.socket(fileno=os.dup(0))
        server.timeout = 1
        server.served = False
        while not server.served:
            check_parent()
            server.handle_request()


if __name__ == "__main__":
    serve(sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
