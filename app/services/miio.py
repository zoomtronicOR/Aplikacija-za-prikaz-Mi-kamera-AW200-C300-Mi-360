"""Minimal local miIO client (UDP 54321, AES-128-CBC with a key derived from the device token)."""
import asyncio
import hashlib
import json
import socket
import struct
import time

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PORT = 54321
HELLO = bytes.fromhex("21310020" + "ff" * 28)


class MiioError(Exception):
    pass


def _md5(b: bytes) -> bytes:
    return hashlib.md5(b).digest()


def ping(ip: str, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(HELLO, (ip, PORT))
            s.recvfrom(1024)
            return True
        except OSError:
            return False


class MiioDevice:
    def __init__(self, ip: str, token_hex: str, timeout: float = 5.0):
        self.ip, self.token, self.timeout = ip, bytes.fromhex(token_hex), timeout
        self.key = _md5(self.token)
        self.iv = _md5(self.key + self.token)
        self._msg_id = int(time.time()) % 10000
        # one request at a time per device; the camera answers by message id but drops overlapping handshakes
        self._lock = asyncio.Lock()

    def _cipher(self) -> Cipher:
        return Cipher(algorithms.AES(self.key), modes.CBC(self.iv))

    def _encrypt(self, data: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        enc = self._cipher().encryptor()
        return enc.update(padder.update(data) + padder.finalize()) + enc.finalize()

    def _decrypt(self, data: bytes) -> bytes:
        dec = self._cipher().decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(dec.update(data) + dec.finalize()) + unpadder.finalize()

    def _send_sync(self, method: str, params: list) -> object:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            try:
                s.sendto(HELLO, (self.ip, PORT))
                hello, _ = s.recvfrom(1024)
                did, stamp = struct.unpack(">II", hello[8:16])

                self._msg_id += 1
                payload = json.dumps({"id": self._msg_id, "method": method, "params": params}).encode()
                data = self._encrypt(payload)
                header = struct.pack(">HHIII", 0x2131, 32 + len(data), 0, did, stamp + 1)
                s.sendto(header + _md5(header + self.token + data) + data, (self.ip, PORT))
                resp, _ = s.recvfrom(4096)
            except OSError as e:
                raise MiioError(f"kamera ne odgovara ({e.__class__.__name__})") from e

        msg = json.loads(self._decrypt(resp[32:]).rstrip(b"\x00"))
        if "error" in msg:
            raise MiioError(msg["error"].get("message", "nepoznata greška"))
        return msg["result"]

    async def send(self, method: str, params: list) -> object:
        async with self._lock:
            return await asyncio.to_thread(self._send_sync, method, params)
