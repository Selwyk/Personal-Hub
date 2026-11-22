"""Synthetic fixtures and a software WebAuthn authenticator, NOT physical FIDO evidence."""
import hashlib
import json
import secrets

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.security.service import encode, decode

PASSWORD = "Synthetic fixture password 2026!"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def post(client, path, payload=None, method="POST"):
    if path == "/auth/login":
        state = client.get("/api/v1/auth/csrf")
        client.headers["X-CSRF-Token"] = state.json()["csrf_token"]
    response = client.request(method, "/api/v1" + path, json=payload)
    if response.status_code < 400 and "csrf_token" in response.json():
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


class SoftwareAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)

    def register(self, options, origin="http://localhost:5173", flags=0x45):
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1,
                           -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"],
                             "origin": origin}).encode()
        auth = (hashlib.sha256(options["rp"]["id"].encode()).digest() + bytes([flags])
                + bytes(4) + bytes(16) + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id + cose)
        return {"id": encode(self.credential_id), "rawId": encode(self.credential_id),
                "type": "public-key", "response": {"clientDataJSON": encode(client),
                "attestationObject": encode(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})),
                "transports": ["usb"]}}

    def authenticate(self, options, origin="http://localhost:5173", flags=5, count=1,
                     rp="localhost", wrong_signature=False):
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"],
                             "origin": origin}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        if wrong_signature:
            signature = bytes(len(signature))
        return {"id": encode(self.credential_id), "rawId": encode(self.credential_id),
                "type": "public-key", "response": {"clientDataJSON": encode(client),
                "authenticatorData": encode(auth), "signature": encode(signature), "userHandle": None}}


def corrupt_challenge(credential):
    client = json.loads(decode(credential["response"]["clientDataJSON"]))
    client["challenge"] = encode(b"wrong challenge")
    credential["response"]["clientDataJSON"] = encode(json.dumps(client).encode())
    return credential
