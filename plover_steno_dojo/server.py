"""
Steno Dojo WebSocket Extension for Plover

Broadcasts stroke and machine state events over a WebSocket server
so the Steno Dojo app can receive real-time steno input.

No external dependencies — uses only Python standard library.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading

PORT = 8086
VERSION = "0.3.0"


# ─── Minimal WebSocket Server ─────────────────────────────────────────────────

class _WebSocketServer:
    """Thread-based WebSocket server using only stdlib."""

    _MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host: str = "localhost", port: int = PORT, get_dictionaries=None):
        self._host = host
        self._port = port
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._server_sock: socket.socket | None = None
        self._running = False
        self._get_dictionaries = get_dictionaries

    def start(self) -> None:
        self._running = True
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._host, self._port))
        self._server_sock.listen(10)
        self._server_sock.settimeout(1.0)
        t = threading.Thread(target=self._accept_loop, daemon=True, name="steno-dojo-ws")
        t.start()

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        with self._lock:
            for client in list(self._clients):
                try:
                    client.close()
                except OSError:
                    pass
            self._clients.clear()

    def broadcast(self, payload: dict) -> None:
        frame = self._make_frame(json.dumps(payload).encode())
        with self._lock:
            dead: set[socket.socket] = set()
            for client in self._clients:
                try:
                    client.sendall(frame)
                except OSError:
                    dead.add(client)
            self._clients -= dead

    # ── internals ─────────────────────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_sock.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(conn,),
                daemon=True,
            ).start()

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            # Read the HTTP upgrade request
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                raw += chunk

            # Parse the WebSocket key and Origin out of headers
            headers: dict[str, str] = {}
            for line in raw.decode("utf-8", errors="replace").split("\r\n")[1:]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip().lower()] = v.strip()

            # Origin verification to prevent Cross-Site WebSocket Hijacking (CSWSH)
            # Browsers will reliably send the Origin header. Non-browser clients might not.
            # We strictly reject any Origin that isn't localhost or Tauri's custom protocols.
            origin = headers.get("origin")
            if origin:
                allowed_prefixes = (
                    "http://localhost",
                    "https://localhost",
                    "tauri://localhost",
                    "https://tauri.localhost",
                    "http://tauri.localhost",
                    "asset://localhost",
                )
                if not origin.startswith(allowed_prefixes):
                    conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    return

            key = headers.get("sec-websocket-key", "")
            accept = base64.b64encode(
                hashlib.sha1((key + self._MAGIC).encode()).digest()
            ).decode()

            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )

            with self._lock:
                self._clients.add(conn)

            # Send a hello message so the app can verify the plugin version
            hello = self._make_frame(json.dumps({"type": "hello", "version": VERSION}).encode())
            conn.sendall(hello)

            # Send active dictionaries if available
            if self._get_dictionaries:
                try:
                    dicts = self._get_dictionaries()
                    if dicts:
                        dicts_msg = self._make_frame(json.dumps({
                            "type": "dictionaries", 
                            "dictionaries": dicts
                        }).encode())
                        conn.sendall(dicts_msg)
                except Exception:
                    pass

            # Read loop — we don't need client→server messages, just keep alive
            conn.settimeout(60.0)
            while self._running:
                try:
                    header = conn.recv(2)
                except socket.timeout:
                    # Send a ping to check the client is still there
                    try:
                        conn.sendall(b"\x89\x00")
                    except OSError:
                        break
                    continue

                if len(header) < 2:
                    break

                opcode = header[0] & 0x0F
                masked = bool(header[1] & 0x80)
                length = header[1] & 0x7F

                if length == 126:
                    ext = conn.recv(2)
                    length = struct.unpack(">H", ext)[0]
                elif length == 127:
                    ext = conn.recv(8)
                    length = struct.unpack(">Q", ext)[0]

                mask_key = conn.recv(4) if masked else b""
                payload = b""
                while len(payload) < length:
                    chunk = conn.recv(length - len(payload))
                    if not chunk:
                        break
                    payload += chunk

                if opcode == 0x8:  # close frame
                    break
                # pong if ping
                if opcode == 0x9:
                    try:
                        conn.sendall(b"\x8A\x00")
                    except OSError:
                        break

        except Exception:
            pass
        finally:
            with self._lock:
                self._clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _make_frame(data: bytes) -> bytes:
        length = len(data)
        if length <= 125:
            return bytes([0x81, length]) + data
        elif length <= 65535:
            return bytes([0x81, 126]) + struct.pack(">H", length) + data
        else:
            return bytes([0x81, 127]) + struct.pack(">Q", length) + data


# ─── Plover Extension ─────────────────────────────────────────────────────────

class StenoDojo:
    """
    Plover Extension that runs a WebSocket server on ws://localhost:8086/
    and broadcasts stroke + machine state events to connected clients.

    Message format (JSON):
      {"type": "hello",                 "version": "0.2.0"}           ← sent once on connect
      {"type": "stroked",               "stroke": "TEFT"}
      {"type": "machine_state_changed", "machine_type": "Gemini PR", "state": "connected"}
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._server = _WebSocketServer(port=PORT, get_dictionaries=self._get_active_dictionaries)

    def _get_active_dictionaries(self) -> list[str]:
        try:
            dicts = self._engine.config.get("dictionaries", [])
            paths = [str(d.get("path")) for d in dicts if d.get("enabled", False) and d.get("path")]
            
            # Attempt to make paths absolute if they are relative
            abs_paths = []
            config_dir = getattr(self._engine.config_manager, "config_dir", None)
            
            for p in paths:
                if os.path.isabs(p):
                    abs_paths.append(p)
                elif config_dir:
                    abs_paths.append(os.path.join(config_dir, p))
                else:
                    import platform
                    sys_name = platform.system()
                    if sys_name == "Windows":
                        default_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "plover", "plover")
                    elif sys_name == "Darwin":
                        default_dir = os.path.expanduser("~/Library/Application Support/plover")
                    else:
                        default_dir = os.path.expanduser("~/.config/plover")
                    abs_paths.append(os.path.join(default_dir, p))
                    
            return abs_paths
        except Exception:
            return []

    def start(self) -> None:
        self._server.start()
        self._engine.hook_connect("stroked", self._on_stroked)
        self._engine.hook_connect("machine_state_changed", self._on_machine_state_changed)

    def stop(self) -> None:
        self._engine.hook_disconnect("stroked", self._on_stroked)
        self._engine.hook_disconnect("machine_state_changed", self._on_machine_state_changed)
        self._server.stop()

    # ── Plover hooks ──────────────────────────────────────────────────────────

    def _on_stroked(self, stroke) -> None:
        self._server.broadcast({
            "type": "stroked",
            "stroke": stroke.rtfcre,
        })

    def _on_machine_state_changed(self, machine_type: str, machine_state: str) -> None:
        self._server.broadcast({
            "type": "machine_state_changed",
            "machine_type": machine_type,
            "state": machine_state,
        })
