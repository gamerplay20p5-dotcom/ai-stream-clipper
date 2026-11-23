"""A minimal local SMTP server for tests: EHLO, STARTTLS (self-signed certificate), AUTH PLAIN/LOGIN, MAIL, RCPT, DATA.

It lets the tests exercise the REAL senders (the API's smtplib with certificate verification, the commercial
service's nodemailer with requireTLS) instead of a monkeypatched function. Messages are kept in memory.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import email
import email.policy
import ipaddress
import ssl
import threading
from dataclasses import dataclass, field
from pathlib import Path


def make_certificate(directory: Path) -> tuple[Path, Path]:
    """Self-signed certificate for localhost / 127.0.0.1 (doubles as the CA the clients trust)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "smtp-test.crt", directory / "smtp-test.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


@dataclass
class Received:
    sender: str
    recipients: list[str]
    message: email.message.EmailMessage
    tls: bool
    user: str

    @property
    def text(self) -> str:
        part = self.message.get_body(preferencelist=("plain",)) or self.message
        return part.get_content()


@dataclass
class SmtpSink:
    cert: Path
    key: Path
    username: str = "clipper-test"
    password: str = "senha-smtp-de-teste-123"
    messages: list[Received] = field(default_factory=list)
    port: int = 0

    def start(self) -> "SmtpSink":
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self._loop)
            self._server = self._loop.run_until_complete(asyncio.start_server(self._client, "127.0.0.1", 0))
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        assert ready.wait(10), "SMTP sink did not start"
        return self

    def stop(self) -> None:
        async def shutdown():
            self._server.close()
            await self._server.wait_closed()
            for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                task.cancel()

        asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tls, user, sender, rcpts = False, "", "", []
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.cert, self.key)

        async def say(line: str) -> None:
            writer.write((line + "\r\n").encode())
            await writer.drain()

        async def line() -> str:
            return (await reader.readline()).decode(errors="replace").rstrip("\r\n")

        await say("220 localhost ESMTP test sink")
        try:
            while True:
                cmd = await line()
                verb = cmd.split(" ", 1)[0].upper()
                if verb in ("EHLO", "HELO"):
                    caps = ["AUTH PLAIN LOGIN"] if tls else ["STARTTLS"]
                    for cap in ["localhost", *caps[:-1]]:
                        await say(f"250-{cap}")
                    await say(f"250 {caps[-1]}")
                elif verb == "STARTTLS":
                    await say("220 ready for TLS")
                    await writer.start_tls(ctx)
                    tls = True
                elif verb == "AUTH":
                    if not tls:
                        await say("530 must issue STARTTLS first")
                        continue
                    parts = cmd.split(" ")
                    if parts[1].upper() == "PLAIN":
                        raw = parts[2] if len(parts) > 2 else (await say("334 ") or await line())
                        _, u, p = base64.b64decode(raw).decode().split("\0")
                    else:
                        await say("334 VXNlcm5hbWU6")
                        u = base64.b64decode(await line()).decode()
                        await say("334 UGFzc3dvcmQ6")
                        p = base64.b64decode(await line()).decode()
                    if (u, p) == (self.username, self.password):
                        user = u
                        await say("235 authenticated")
                    else:
                        await say("535 authentication failed")
                elif verb == "MAIL":
                    if not user:
                        await say("530 authentication required")
                        continue
                    sender, rcpts = cmd.split(":", 1)[1].split()[0].strip("<>"), []
                    await say("250 ok")
                elif verb == "RCPT":
                    rcpts.append(cmd.split(":", 1)[1].split()[0].strip("<>"))
                    await say("250 ok")
                elif verb == "DATA":
                    await say("354 end with .")
                    data = bytearray()
                    while True:
                        chunk = await reader.readline()
                        if chunk in (b".\r\n", b".\n"):
                            break
                        data += chunk[1:] if chunk.startswith(b"..") else chunk
                    msg = email.message_from_bytes(bytes(data), policy=email.policy.default)
                    self.messages.append(Received(sender, rcpts, msg, tls, user))
                    await say("250 queued")
                elif verb in ("RSET", "NOOP"):
                    await say("250 ok")
                elif verb == "QUIT" or not cmd:
                    await say("221 bye")
                    break
                else:
                    await say("502 not implemented")
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
