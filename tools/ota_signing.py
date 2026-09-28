#!/usr/bin/env python3
"""Local ECDSA P-256 signing helpers for Keg Scale OTA manifests."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM = "ECDSA-P256-SHA256"
SIGNATURE_FORMAT = 1
PUBLIC_KEY_FORMAT = 1


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import (
            decode_dss_signature,
            encode_dss_signature,
        )
    except ImportError as exc:
        raise SystemExit(
            "Python package 'cryptography' is required for OTA signing.\n"
            "Install it with: python -m pip install cryptography"
        ) from exc
    return InvalidSignature, hashes, serialization, ec, decode_dss_signature, encode_dss_signature


def public_key_raw(public_key):
    numbers = public_key.public_numbers()
    return b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")


def key_id_for_public_key(public_key):
    return hashlib.sha256(public_key_raw(public_key)).hexdigest()[:16]


def load_private_key(path):
    _, _, serialization, ec, _, _ = _crypto()
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise SystemExit(f"Signing key is not an EC private key: {path}")
    if not isinstance(key.curve, ec.SECP256R1):
        raise SystemExit(f"Signing key must use P-256/secp256r1: {path}")
    return key


def load_public_metadata(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"format", "algorithm", "channel", "key_id", "public_key"}
    if not required.issubset(data):
        raise SystemExit(f"Public-key metadata is incomplete: {path}")
    if data["format"] != PUBLIC_KEY_FORMAT or data["algorithm"] != ALGORITHM:
        raise SystemExit(f"Unsupported public-key metadata: {path}")
    raw = base64.b64decode(data["public_key"], validate=True)
    if len(raw) != 65 or raw[0] != 0x04:
        raise SystemExit(f"Public key must be a 65-byte uncompressed P-256 point: {path}")
    if hashlib.sha256(raw).hexdigest()[:16] != data["key_id"]:
        raise SystemExit(f"Public-key key_id does not match key material: {path}")
    return data, raw


def public_key_from_raw(raw):
    _, _, _, ec, _, _ = _crypto()
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def sign_bytes(private_key, payload):
    _, hashes, _, ec, decode_dss_signature, _ = _crypto()
    der = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify_bytes(public_key, payload, signature):
    InvalidSignature, hashes, _, ec, _, encode_dss_signature = _crypto()
    if len(signature) != 64:
        return False
    der = encode_dss_signature(
        int.from_bytes(signature[:32], "big"),
        int.from_bytes(signature[32:], "big"),
    )
    try:
        public_key.verify(der, payload, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def signature_document(channel, key_id, signature):
    return {
        "format": SIGNATURE_FORMAT,
        "algorithm": ALGORITHM,
        "channel": channel,
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def signature_bytes(document):
    if document.get("format") != SIGNATURE_FORMAT:
        raise SystemExit("Unsupported manifest signature format")
    if document.get("algorithm") != ALGORITHM:
        raise SystemExit("Unsupported manifest signature algorithm")
    raw = base64.b64decode(document.get("signature", ""), validate=True)
    if len(raw) != 64:
        raise SystemExit("Manifest signature must decode to 64 bytes")
    return raw


def generate(channel, private_dir, public_dir):
    _, _, serialization, ec, _, _ = _crypto()
    private_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    private_path = private_dir / f"ota-{channel}-private.pem"
    public_path = public_dir / f"ota-{channel}-public-key.json"
    if private_path.exists() or public_path.exists():
        raise SystemExit(
            f"Refusing to overwrite existing key material for channel {channel}.\n"
            f"Private: {private_path}\nPublic: {public_path}"
        )

    key = ec.generate_private_key(ec.SECP256R1())
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = key.public_key()
    raw = public_key_raw(public)
    key_id = key_id_for_public_key(public)
    metadata = {
        "format": PUBLIC_KEY_FORMAT,
        "algorithm": ALGORITHM,
        "channel": channel,
        "key_id": key_id,
        "public_key": base64.b64encode(raw).decode("ascii"),
    }
    public_path.write_bytes((json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
    print(f"{channel}: key_id={key_id}")
    print(f"  private: {private_path}")
    print(f"  public:  {public_path}")


def sign_manifest(manifest_path, channel, private_key_path, public_metadata_path, output_path):
    manifest = Path(manifest_path).read_bytes()
    private_key = load_private_key(private_key_path)
    metadata, expected_raw = load_public_metadata(public_metadata_path)

    if public_key_raw(private_key.public_key()) != expected_raw:
        raise SystemExit(f"Private key does not match committed {channel} public key metadata")
    if metadata["channel"] != channel:
        raise SystemExit(
            f"Public key channel mismatch: expected {channel}, got {metadata['channel']}"
        )

    signature = sign_bytes(private_key, manifest)
    document = signature_document(channel, metadata["key_id"], signature)
    Path(output_path).write_bytes((json.dumps(document, indent=2) + "\n").encode("utf-8"))

    if not verify_bytes(public_key_from_raw(expected_raw), manifest, signature):
        raise SystemExit("Internal verification failed after signing manifest")
    return document


def verify_manifest(manifest_path, signature_path, public_metadata_path):
    manifest = Path(manifest_path).read_bytes()
    document = json.loads(Path(signature_path).read_text(encoding="utf-8"))
    metadata, raw = load_public_metadata(public_metadata_path)
    if document.get("key_id") != metadata["key_id"]:
        return False
    if document.get("channel") != metadata["channel"]:
        return False
    return verify_bytes(public_key_from_raw(raw), manifest, signature_bytes(document))


def self_test():
    _, _, _, ec, _, _ = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    payload = b'{"version":"V9.9.9"}\n'
    signature = sign_bytes(key, payload)
    assert len(signature) == 64
    assert verify_bytes(key.public_key(), payload, signature)
    assert not verify_bytes(key.public_key(), payload + b" ", signature)
    assert not verify_bytes(wrong_key.public_key(), payload, signature)
    assert not verify_bytes(key.public_key(), payload, signature[:-1])
    print("PASS: OTA signing self-test (valid, tampered, wrong-key, truncated)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate channel signing keys")
    p_gen.add_argument("--channel", action="append", choices=["dev", "production"], required=True)
    p_gen.add_argument("--private-dir", type=Path, required=True)
    p_gen.add_argument("--public-dir", type=Path, default=ROOT / "keys")

    p_sign = sub.add_parser("sign", help="Sign one manifest")
    p_sign.add_argument("--manifest", type=Path, required=True)
    p_sign.add_argument("--channel", choices=["dev", "production"], required=True)
    p_sign.add_argument("--private-key", type=Path, required=True)
    p_sign.add_argument("--public-key", type=Path, required=True)
    p_sign.add_argument("--output", type=Path, required=True)

    p_verify = sub.add_parser("verify", help="Verify one manifest signature")
    p_verify.add_argument("--manifest", type=Path, required=True)
    p_verify.add_argument("--signature", type=Path, required=True)
    p_verify.add_argument("--public-key", type=Path, required=True)

    sub.add_parser("self-test", help="Run signing/tamper regression checks")
    args = parser.parse_args()

    if args.command == "generate":
        for channel in args.channel:
            generate(channel, args.private_dir.resolve(), args.public_dir.resolve())
    elif args.command == "sign":
        sign_manifest(args.manifest, args.channel, args.private_key, args.public_key, args.output)
        print(f"Signed {args.manifest} -> {args.output}")
    elif args.command == "verify":
        ok = verify_manifest(args.manifest, args.signature, args.public_key)
        print("VALID" if ok else "INVALID")
        raise SystemExit(0 if ok else 1)
    else:
        self_test()


if __name__ == "__main__":
    main()
