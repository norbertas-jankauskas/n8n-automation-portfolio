"""Bounded CONNECT-only TCP proxy with metadata-only event logging."""

import argparse
import ipaddress
import json
import re
import select
import socket
import threading
import time


def authority(value):
    """Parse a literal CONNECT authority without userinfo, paths or DNS aliases."""
    match = re.fullmatch(r"(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+):([0-9]+)", value)
    if match is None or not 1 <= int(match[2]) <= 65535:
        raise ValueError("invalid authority")
    host = match[1].strip("[]").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                   for label in host.split("."))):
            raise ValueError("invalid host") from None
    return host, int(match[2])


class Proxy:
    """One instance shares total tunnel and byte budgets; idle caps are per tunnel.

    The connector is injectable so tests never resolve or contact a real host.
    Client headers are bounded and discarded, never passed to the upstream.
    """

    def __init__(self, allow, max_tunnels=50, max_bytes=5242880, idle_seconds=30,
                 connector=socket.create_connection, logger=None):
        self.host, self.port = authority(allow)
        if max_tunnels <= 0 or max_bytes <= 0 or idle_seconds <= 0:
            raise ValueError("caps must be positive")
        self.max_tunnels = max_tunnels
        self.max_bytes = max_bytes
        self.idle_seconds = idle_seconds
        self.connector = connector
        self.logger = logger
        self.lock = threading.Lock()
        self.tunnels = 0
        self.bytes_used = 0

    def event(self, event, host, port, up=0, down=0, reason=""):
        entry = {"event": event, "host": host, "port": port,
                 "bytes_up": up, "bytes_down": down, "reason": reason}
        with self.lock:
            if self.logger is None:
                print("APC-PROXY " + json.dumps(entry, sort_keys=True), flush=True)
            else:
                self.logger("APC-PROXY " + json.dumps(entry, sort_keys=True))

    def handle(self, client):
        host, port = "", 0
        up = down = 0
        upstream = None
        allowed = False
        reason = "peer_closed"
        try:
            client.settimeout(self.idle_seconds)
            header = bytearray()
            deadline = time.monotonic() + self.idle_seconds
            while not header.endswith(b"\r\n\r\n"):
                client.settimeout(max(0.001, deadline - time.monotonic()))
                part = client.recv(1)
                if not part:
                    return
                header.extend(part)
                if len(header) > 16384 or time.monotonic() >= deadline:
                    self.event("deny", host, port, reason="invalid_headers")
                    client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    return
            try:
                method, target, version = bytes(header).split(b"\r\n", 1)[0].decode("ascii").split(" ")
                if method != "CONNECT":
                    self.event("deny", host, port, reason="method")
                    client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                    return
                host, port = authority(target)
                if version not in ("HTTP/1.0", "HTTP/1.1") or (host, port) != (self.host, self.port):
                    raise ValueError
            except (ValueError, UnicodeError):
                self.event("deny", host, port, reason="target")
                client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            with self.lock:
                cap_reason = ("max_tunnels" if self.tunnels >= self.max_tunnels else
                              "max_bytes" if self.bytes_used >= self.max_bytes else "")
                if not cap_reason:
                    self.tunnels += 1
            if cap_reason:
                self.event("cap_exceeded", host, port, reason=cap_reason)
                client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            upstream = self.connector((host, port), timeout=self.idle_seconds)
            upstream.settimeout(self.idle_seconds)
            client.settimeout(self.idle_seconds)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            allowed = True
            self.event("allow", host, port, reason="target")
            peers = {client: upstream, upstream: client}
            while peers:
                readable, _, _ = select.select(list(peers), [], [], self.idle_seconds)
                if not readable:
                    reason = "idle_seconds"
                    self.event("cap_exceeded", host, port, up, down, reason)
                    break
                for source in readable:
                    with self.lock:
                        remaining = self.max_bytes - self.bytes_used
                    data = source.recv(min(65536, remaining + 1))
                    if not data:
                        destination = peers.pop(source)
                        destination.shutdown(socket.SHUT_WR)
                        continue
                    with self.lock:
                        capped = self.bytes_used + len(data) > self.max_bytes
                        if not capped:
                            # Reserve before I/O so concurrent tunnels cannot overspend.
                            # Failed writes retain their reservation conservatively.
                            self.bytes_used += len(data)
                    if capped:
                        reason = "max_bytes"
                        self.event("cap_exceeded", host, port, up, down, reason)
                        return
                    peers[source].sendall(data)
                    if source is client:
                        up += len(data)
                    else:
                        down += len(data)
        except TimeoutError:
            reason = "idle_seconds"
            self.event("cap_exceeded", host, port, up, down, reason)
        except (OSError, ValueError):
            reason = "io_error"
            if not allowed:
                self.event("deny", host, port, reason=reason)
        finally:
            if upstream is not None:
                upstream.close()
            client.close()
            if allowed:
                self.event("close", host, port, up, down, reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, default=3128)
    parser.add_argument("--allow", required=True)
    parser.add_argument("--max-tunnels", type=int, default=50)
    parser.add_argument("--max-bytes", type=int, default=5242880)
    parser.add_argument("--idle-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    proxy = Proxy(args.allow, args.max_tunnels, args.max_bytes, args.idle_seconds)
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", args.listen_port))
        server.listen(64)
        while True:
            client, _ = server.accept()
            threading.Thread(target=proxy.handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
