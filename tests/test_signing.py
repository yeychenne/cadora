"""Evidence-pack signing: the hash floor always runs; the optional signature is real."""

import json
import shutil
import subprocess

import pytest

from cadora.report import write_report
from cadora.signing import sign_pack, verify_pack

HAS_SSH_KEYGEN = shutil.which("ssh-keygen") is not None
needs_ssh = pytest.mark.skipif(not HAS_SSH_KEYGEN, reason="ssh-keygen not available")


def _make_run(root, run_id="r"):
    d = root / run_id
    d.mkdir()
    (d / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "topology": "t", "executor": "claude", "ok": True, "nodes": []})
    )
    (d / "output.txt").write_text("hello evidence\n")
    write_report(str(root), run_id)
    return d


def _ssh_key(path):
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-q"], check=True
    )
    return str(path)


def test_hash_floor_verifies_unsigned_pack_and_catches_tampering(tmp_path):
    _make_run(tmp_path)
    res = verify_pack(str(tmp_path), "r")
    assert res.hashes_ok and res.signature == "absent" and res.ok  # checksummed, not signed

    (tmp_path / "r" / "output.txt").write_text("TAMPERED\n")
    res2 = verify_pack(str(tmp_path), "r")
    assert not res2.hashes_ok
    assert "output.txt" in res2.mismatched
    assert not res2.ok  # tamper-evident even without a signature


@needs_ssh
def test_ssh_sign_verify_roundtrip_is_valid(tmp_path):
    _make_run(tmp_path)
    key = _ssh_key(tmp_path / "id_ed25519")
    meta = sign_pack(str(tmp_path), "r", key=key)
    assert meta["fingerprint"] and (tmp_path / "r" / "report" / "checksums.txt.sig").is_file()

    res = verify_pack(str(tmp_path), "r")  # self-attesting (embedded pubkey)
    assert res.signature == "valid" and res.ok
    assert res.fingerprint == meta["fingerprint"]


@needs_ssh
def test_tampering_after_signing_fails_verification(tmp_path):
    _make_run(tmp_path)
    sign_pack(str(tmp_path), "r", key=_ssh_key(tmp_path / "id"))
    (tmp_path / "r" / "output.txt").write_text("changed after signing\n")
    res = verify_pack(str(tmp_path), "r")
    assert not res.ok  # the hash floor catches it regardless of the signature


@needs_ssh
def test_authenticated_verify_against_allowed_signers(tmp_path):
    _make_run(tmp_path)
    key = _ssh_key(tmp_path / "id")
    meta = sign_pack(str(tmp_path), "r", key=key, identity="alice@example.com")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"alice@example.com {meta['public_key']}\n")
    res = verify_pack(str(tmp_path), "r", allowed_signers=str(allowed))
    assert res.signature == "valid" and res.ok


@needs_ssh
def test_wrong_key_does_not_verify(tmp_path):
    _make_run(tmp_path)
    sign_pack(str(tmp_path), "r", key=_ssh_key(tmp_path / "signer"))
    # An allowed-signers file listing a DIFFERENT key must not validate the signature.
    other = _ssh_key(tmp_path / "other")
    pub = subprocess.run(["ssh-keygen", "-y", "-f", other], capture_output=True, text=True).stdout.strip()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"cadora-pack-signer {pub}\n")
    res = verify_pack(str(tmp_path), "r", allowed_signers=str(allowed))
    assert res.signature == "invalid" and not res.ok


def test_pluggable_signer_and_verifier(tmp_path):
    _make_run(tmp_path)
    # A trivial external signer that just produces the .sig file, and a verifier that checks it.
    sign_pack(str(tmp_path), "r", signer="cp {file} {sig}")
    assert (tmp_path / "r" / "report" / "checksums.txt.sig").is_file()
    res = verify_pack(str(tmp_path), "r", verifier="test -f {sig}")
    assert res.signature == "valid" and res.ok
