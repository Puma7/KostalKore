"""The integration ships its own pykoplenti, encrypted with ``cryptography``.

KORE used to install pykoplenti from PyPI, which pins ``pycryptodome<4``. A
mixed or broken ``pycryptodome`` installation in Home Assistant then failed the
login ("cannot import name 'MessageTooLongError' from 'Crypto'"). The bundled
copy uses ``cryptography``'s AES-GCM, which Home Assistant itself depends on.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from custom_components.kostal_kore.pykoplenti import ApiClient, AuthenticationException
from custom_components.kostal_kore.pykoplenti.api import encrypt_session_token

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "kostal_kore"


def test_session_token_encryption_matches_pycryptodome() -> None:
    # Reference values produced by pycryptodome 3.24.1 and 4.2.0:
    # AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(token)
    cipher_text, tag = encrypt_session_token(bytes(range(32)), bytes(range(16)), b"session-token-1234567890")
    assert cipher_text.hex() == "1409d305508b2405e990b04c3b891d8167875e9750b5ea73"
    assert tag.hex() == "3be461cbdcac82be24dd61afb73af9b1"


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = body

    async def json(self) -> dict[str, Any]:
        return self._body


class _FakeInverter:
    """Server side of the PLENTICORE login: SCRAM-SHA256, then an AES-GCM session token."""

    def __init__(self, password: str) -> None:
        self.password = password
        self.salt, self.rounds, self.server_nonce = b"0123456789abcdef", 29000, b"server-nonce"
        self.token = "session-token-1234567890"
        self.received_token: str | None = None
        self._auth_msg = ""

    def _keys(self) -> tuple[bytes, bytes, bytes]:
        salted = hashlib.pbkdf2_hmac("sha256", self.password.encode(), self.salt, self.rounds)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        return client_key, hashlib.sha256(client_key).digest(), server_key

    @asynccontextmanager
    async def request(self, method: str, url: Any, json: dict[str, Any]):  # noqa: A002
        path = str(url).rsplit("/api/v1/", 1)[-1]
        yield getattr(self, "_" + path.replace("/", "_"))(json)

    def _auth_start(self, body: dict[str, Any]) -> _Response:
        self._auth_msg = (
            f"n={body['username']},r={body['nonce']},r={b64encode(self.server_nonce).decode()},"
            f"s={b64encode(self.salt).decode()},i={self.rounds},c=biws,r={b64encode(self.server_nonce).decode()}"
        )
        return _Response(200, {"nonce": b64encode(self.server_nonce).decode(), "transactionId": b64encode(b"tx").decode(),
                               "salt": b64encode(self.salt).decode(), "rounds": self.rounds})

    def _auth_finish(self, body: dict[str, Any]) -> _Response:
        client_key, stored_key, server_key = self._keys()
        signature = hmac.new(stored_key, self._auth_msg.encode(), hashlib.sha256).digest()
        proof = b64decode(body["proof"])
        if hashlib.sha256(bytes(a ^ b for a, b in zip(proof, signature))).digest() != stored_key:
            return _Response(400, {"message": "authentication failed"})
        server_signature = hmac.new(server_key, self._auth_msg.encode(), hashlib.sha256).digest()
        return _Response(200, {"token": self.token, "signature": b64encode(server_signature).decode()})

    def _auth_create_session(self, body: dict[str, Any]) -> _Response:
        client_key, stored_key, _ = self._keys()
        session_key = hmac.new(stored_key, b"Session Key", hashlib.sha256)
        session_key.update(self._auth_msg.encode())
        session_key.update(client_key)
        sealed = b64decode(body["payload"]) + b64decode(body["tag"])
        self.received_token = AESGCM(session_key.digest()).decrypt(b64decode(body["iv"]), sealed, None).decode()
        return _Response(200, {"sessionId": "session-1"})


@pytest.mark.parametrize(("service_code", "expected_token"), [
    (None, "session-token-1234567890"),
    ("12345", "session-token-1234567890:12345"),
])
async def test_login_creates_a_session_the_inverter_can_decrypt(service_code: str | None, expected_token: str) -> None:
    inverter = _FakeInverter("secret")
    client = ApiClient(inverter, "192.0.2.1")  # type: ignore[arg-type]
    await client.login("secret", service_code=service_code)
    assert inverter.received_token == expected_token
    assert client.session_id == "session-1"


async def test_login_with_a_wrong_password_is_rejected() -> None:
    client = ApiClient(_FakeInverter("secret"), "192.0.2.1")  # type: ignore[arg-type]
    with pytest.raises(AuthenticationException):
        await client.login("wrong")


class _ExpiringInverter:
    """Answers 401 until the client has logged in again, then returns a value."""

    def __init__(self) -> None:
        self.logged_in = False
        self.logins = 0
        self.lock: asyncio.Lock | None = None
        self.requested_under_lock = False

    @asynccontextmanager
    async def request(self, method: str, url: Any, **kwargs: Any):
        await asyncio.sleep(0)  # let concurrent requests run into the 401 as well
        self.requested_under_lock |= bool(self.lock and self.lock.locked())
        if not self.logged_in:
            yield _Response(401, {"message": "session expired"})
        else:
            yield _Response(200, [{"moduleid": "moda", "processdata": [{"id": "procb", "unit": "W", "value": 7}]}])

    async def login(self) -> None:
        self.logins += 1
        await asyncio.sleep(0)
        self.logged_in = True


def _client_for(inverter: _ExpiringInverter) -> ApiClient:
    client = ApiClient(inverter, "192.0.2.1")  # type: ignore[arg-type]

    async def login() -> None:  # like ApiClient._login: a successful login sets a new session
        await inverter.login()
        client.session_id = f"session-{inverter.logins}"

    client._login = login  # type: ignore[method-assign]
    inverter.lock = client._login_lock
    return client


async def test_expired_session_logs_in_again_and_retries() -> None:
    """A 401 on a request triggers one re-login and a successful retry (ported from upstream)."""
    inverter = _ExpiringInverter()
    values = await _client_for(inverter).get_process_data_values("moda", "procb")
    assert inverter.logins == 1
    assert values["moda"]["procb"].value == 7
    assert not inverter.requested_under_lock  # only the login holds the lock, requests run outside it


async def test_concurrent_requests_share_one_re_login() -> None:
    """Two requests that both find the session expired trigger a single login."""
    inverter = _ExpiringInverter()
    client = _client_for(inverter)
    results = await asyncio.gather(*(client.get_process_data_values("moda", "procb") for _ in range(2)))
    assert inverter.logins == 1
    assert [result["moda"]["procb"].value for result in results] == [7, 7]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
    return {name.split(".")[0] for name in names}


def test_integration_uses_neither_pycryptodome_nor_the_pypi_pykoplenti() -> None:
    offenders = {
        str(path.relative_to(COMPONENT)): sorted(_imported_modules(path) & {"Crypto", "Cryptodome", "pykoplenti"})
        for path in COMPONENT.rglob("*.py")
    }
    assert {path: names for path, names in offenders.items() if names} == {}
    manifest = json.loads((COMPONENT / "manifest.json").read_text("utf-8"))
    assert not [req for req in manifest["requirements"] if req.lower().startswith(("pykoplenti", "pycryptodome"))]
