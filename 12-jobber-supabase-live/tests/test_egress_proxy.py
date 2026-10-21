"""CONNECT transport tests use socketpairs; DNS and outbound sockets are forbidden."""

import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import threading
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "boundary" / "egress_proxy.py"
SPEC = importlib.util.spec_from_file_location("egress_proxy", SCRIPT)
PROXY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROXY)


class PairStream:
    """Duplex socketpair stream using read/write, allowed by restricted sandboxes.

    Each direction has its own socketpair, so closing the write endpoint gives
    a real EOF without the sandbox-blocked send/recv/shutdown syscalls.
    """

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.timeout = 2

    def fileno(self):
        return self.reader.fileno()

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, count):
        if not select.select([self.reader], [], [], self.timeout)[0]:
            raise socket.timeout
        return os.read(self.reader.fileno(), count)

    def sendall(self, data):
        remaining = memoryview(data)
        while remaining:
            if not select.select([], [self.writer], [], self.timeout)[1]:
                raise socket.timeout
            written = os.write(self.writer.fileno(), remaining)
            remaining = remaining[written:]

    def shutdown(self, how):
        if how != socket.SHUT_WR:
            raise ValueError("test stream supports write half-close only")
        self.writer.close()

    def close(self):
        self.writer.close()
        self.reader.close()


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.threads = []
        self.sockets = []
        self.connector = mock.Mock(side_effect=AssertionError("unexpected upstream"))
        self.proxy = PROXY.Proxy("allowed.test:443", connector=self.connector,
                                 logger=self.logs.append, idle_seconds=0.15)
        for name in ("getaddrinfo", "create_connection"):
            patcher = mock.patch.object(socket, name, side_effect=AssertionError("network forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.finish)

    def finish(self):
        for sock in self.sockets:
            sock.close()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def pair(self):
        first_read, second_write = socket.socketpair()
        second_read, first_write = socket.socketpair()
        pair = (PairStream(first_read, first_write), PairStream(second_read, second_write))
        self.sockets.extend(pair)
        return pair

    def start(self, request=b"CONNECT allowed.test:443 HTTP/1.1\r\n\r\n"):
        client, server = self.pair()
        thread = threading.Thread(target=self.proxy.handle, args=(server,))
        self.threads.append(thread)
        thread.start()
        client.sendall(request)
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            data = client.recv(1)
            if not data:
                break
            response.extend(data)
        return client, bytes(response), thread

    def upstream(self):
        relay, upstream = self.pair()
        self.connector.side_effect = None
        self.connector.return_value = relay
        return upstream

    def events(self):
        return [json.loads(line[len("APC-PROXY "):]) for line in self.logs]

    def test_allow_bidirectional_half_close_and_metadata_only(self):
        upstream = self.upstream()
        client, response, thread = self.start(
            b"CONNECT allowed.test:443 HTTP/1.1\r\nSecret-Header: DO_NOT_LOG\r\n\r\n")
        self.assertIn(b"200", response)
        client.sendall(b"PRIVATE_REQUEST")
        self.assertEqual(upstream.recv(100), b"PRIVATE_REQUEST")
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(upstream.recv(1), b"")
        upstream.sendall(b"PRIVATE_RESPONSE")
        upstream.shutdown(socket.SHUT_WR)
        self.assertEqual(client.recv(100), b"PRIVATE_RESPONSE")
        thread.join(2)
        self.assertEqual([entry["event"] for entry in self.events()], ["allow", "close"])
        self.assertEqual((self.events()[-1]["bytes_up"], self.events()[-1]["bytes_down"]), (15, 16))
        for entry, line in zip(self.events(), self.logs):
            self.assertTrue(line.startswith("APC-PROXY "))
            self.assertEqual(set(entry), {"event", "host", "port", "bytes_up", "bytes_down", "reason"})
        for secret in ("PRIVATE", "Secret-Header", "DO_NOT_LOG"):
            self.assertNotIn(secret, "".join(self.logs))
        self.connector.assert_called_once_with(("allowed.test", 443), timeout=0.15)

    def test_denied_host_port_literals_and_malformed_authorities(self):
        for target in ("other.test:443", "allowed.test:444", "127.0.0.1:443", "[::1]:443",
                       "user@allowed.test:443", "allowed.test:443/path", "allowed.test", "allowed.test:0",
                       "allowed.test:65536", "allowed.test.:443", "allowed.test.evil:443"):
            with self.subTest(target=target):
                _, response, thread = self.start(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
                self.assertIn(b"403", response)
                thread.join(2)
        self.connector.assert_not_called()
        self.assertTrue(all(event["event"] == "deny" for event in self.events()))

    def test_non_connect_is_405(self):
        for method in ("GET", "POST", "connect"):
            _, response, thread = self.start(f"{method} allowed.test:443 HTTP/1.1\r\n\r\n".encode())
            self.assertIn(b"405", response)
            thread.join(2)
        self.connector.assert_not_called()

    def test_case_insensitive_host(self):
        upstream = self.upstream()
        client, response, thread = self.start(b"CONNECT ALLOWED.TeSt:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b"200", response)
        client.close()
        upstream.close()
        thread.join(2)
        self.assertEqual(self.events()[0]["host"], "allowed.test")

    def test_literal_allowed_only_when_allow_host_itself_is_literal(self):
        self.proxy = PROXY.Proxy("127.0.0.1:443", connector=self.connector, logger=self.logs.append)
        upstream = self.upstream()
        client, response, thread = self.start(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
        self.assertIn(b"200", response)
        client.close()
        upstream.close()
        thread.join(2)

    def test_combined_byte_cap_in_both_directions(self):
        self.proxy.max_bytes = 5
        upstream = self.upstream()
        client, response, thread = self.start()
        client.sendall(b"abc")
        self.assertEqual(upstream.recv(3), b"abc")
        upstream.sendall(b"de")
        self.assertEqual(client.recv(2), b"de")
        client.sendall(b"f")
        thread.join(2)
        self.assertEqual([event["event"] for event in self.events()], ["allow", "cap_exceeded", "close"])
        self.assertEqual(self.events()[1]["reason"], "max_bytes")
        self.assertEqual(self.events()[-1]["bytes_up"] + self.events()[-1]["bytes_down"], 5)

    def test_idle_cap_closes(self):
        self.upstream()
        _, response, thread = self.start()
        self.assertIn(b"200", response)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.events()[-2]["reason"], "idle_seconds")

    def test_lifetime_tunnel_cap(self):
        self.proxy.max_tunnels = 1
        upstream = self.upstream()
        client, _, thread = self.start()
        client.close()
        upstream.close()
        thread.join(2)
        _, response, thread = self.start()
        self.assertIn(b"403", response)
        thread.join(2)
        self.assertEqual(self.events()[-1]["reason"], "max_tunnels")
        self.assertEqual(self.connector.call_count, 1)

    def test_connect_failure_is_sanitized(self):
        self.connector.side_effect = OSError("DO_NOT_LOG")
        _, response, thread = self.start()
        thread.join(2)
        self.assertNotIn(b"200", response)
        self.assertNotIn("DO_NOT_LOG", "".join(self.logs))
        self.assertEqual(self.events()[0]["reason"], "io_error")

    def test_stdout_events_are_flushed(self):
        proxy = PROXY.Proxy("allowed.test:443")
        with mock.patch("builtins.print") as printer:
            proxy.event("close", "allowed.test", 443)
        self.assertTrue(printer.call_args.kwargs["flush"])
        self.assertTrue(printer.call_args.args[0].startswith("APC-PROXY "))

    def test_invalid_policy_caps_and_authorities(self):
        for value in ("*.test:443", "http://allowed.test:443", "allowed.test:bad", "[invalid]:443"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PROXY.Proxy(value)
        for kwargs in ({"max_tunnels": 0}, {"max_bytes": -1}, {"idle_seconds": 0}):
            with self.assertRaises(ValueError):
                PROXY.Proxy("allowed.test:443", **kwargs)

    def test_upstream_write_timeout_logs_idle_cap_and_close(self):
        self.upstream()
        relay = self.connector.return_value
        relay.sendall = mock.Mock(side_effect=socket.timeout)
        client, response, thread = self.start()
        self.assertIn(b"200", response)
        client.sendall(b"DO_NOT_LOG")
        thread.join(2)
        self.assertEqual([event["event"] for event in self.events()], ["allow", "cap_exceeded", "close"])
        self.assertEqual(self.events()[-1]["reason"], "idle_seconds")
        self.assertNotIn("DO_NOT_LOG", "".join(self.logs))

    def test_oversized_headers_are_bounded_and_not_logged(self):
        client, server = self.pair()
        client.sendall(b"CONNECT allowed.test:443 HTTP/1.1\r\nX: " + b"q" * 16384 + b"\r\n\r\n")
        thread = threading.Thread(target=self.proxy.handle, args=(server,))
        self.threads.append(thread)
        thread.start()
        self.assertIn(b"403", client.recv(100))
        thread.join(2)
        self.assertEqual(self.events()[0]["reason"], "invalid_headers")
        self.assertNotIn("qqqq", "".join(self.logs))
        self.connector.assert_not_called()

    def test_byte_budget_survives_closed_tunnels_and_combines_both_directions(self):
        self.proxy.max_bytes = 5
        upstream = self.upstream()
        client, response, thread = self.start()
        self.assertIn(b"200", response)
        client.sendall(b"abc")
        self.assertEqual(upstream.recv(3), b"abc")
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(upstream.recv(1), b"")
        upstream.shutdown(socket.SHUT_WR)
        self.assertEqual(client.recv(1), b"")
        thread.join(2)

        upstream = self.upstream()
        client, response, thread = self.start()
        self.assertIn(b"200", response)
        upstream.sendall(b"de")
        self.assertEqual(client.recv(2), b"de")
        client.sendall(b"f")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(upstream.recv(1), b"")
        self.assertEqual(self.proxy.bytes_used, 5)
        closes = [event for event in self.events() if event["event"] == "close"]
        self.assertEqual([(event["bytes_up"], event["bytes_down"]) for event in closes],
                         [(3, 0), (0, 2)])
        self.assertEqual(closes[-1]["reason"], "max_bytes")

        _, response, thread = self.start()
        self.assertIn(b"403", response)
        thread.join(2)
        self.assertEqual(self.connector.call_count, 2)
        self.assertEqual(self.events()[-1]["reason"], "max_bytes")

    def test_concurrent_tunnels_reserve_from_one_atomic_byte_budget(self):
        self.proxy.max_bytes = 5
        barrier = threading.Barrier(2)
        handle = self.proxy.handle

        def synchronized_handle(server):
            receive = server.recv

            def recv(count):
                data = receive(count)
                if count > 1 and data:
                    barrier.wait(2)
                return data

            server.recv = recv
            handle(server)

        with mock.patch.object(self.proxy, "handle", side_effect=synchronized_handle):
            first_upstream = self.upstream()
            first, response, first_thread = self.start()
            self.assertIn(b"200", response)
            second_upstream = self.upstream()
            second, response, second_thread = self.start()
            self.assertIn(b"200", response)
            first.sendall(b"abc")
            second.sendall(b"def")
            for thread in (first_thread, second_thread):
                thread.join(2)
                self.assertFalse(thread.is_alive())
        payloads = [first_upstream.recv(10), second_upstream.recv(10)]
        self.assertEqual(sorted(len(data) for data in payloads), [0, 3])
        self.assertEqual(self.proxy.bytes_used, 3)
        closes = [event for event in self.events() if event["event"] == "close"]
        self.assertEqual(sum(event["bytes_up"] + event["bytes_down"] for event in closes), 3)
        self.assertEqual(sum(event["reason"] == "max_bytes" for event in closes), 1)

    def test_failed_write_retains_shared_byte_reservation(self):
        self.proxy.max_bytes = 3
        self.upstream()
        self.connector.return_value.sendall = mock.Mock(side_effect=OSError("DO_NOT_LOG"))
        client, response, thread = self.start()
        self.assertIn(b"200", response)
        client.sendall(b"abc")
        thread.join(2)
        self.assertEqual(self.proxy.bytes_used, 3)
        _, response, thread = self.start()
        self.assertIn(b"403", response)
        thread.join(2)
        self.assertEqual(self.connector.call_count, 1)
        self.assertNotIn("DO_NOT_LOG", "".join(self.logs))
