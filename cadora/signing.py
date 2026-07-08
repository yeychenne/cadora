"""Detached signatures over an evidence pack — ``cadora sign`` / ``cadora verify``.

The evidence pack (``cadora report``) already writes ``checksums.txt``: the SHA-256 of every
archived run file plus ``report.json``. That makes the pack tamper-*evident* — recompute the
hashes and any change shows. Signing adds the missing half: a detached signature over
``checksums.txt`` binds the whole pack to a key, so a verified pack is tamper-evident **and
attributable**.

Two deliberate properties:

- **The hash check always runs**, with or without a signature — an unsigned pack is still
  verifiable (its integrity, not its origin). So "checksummed" is the always-on floor and
  "signed" is the optional layer on top; the pack's own claims say which it is.
- **Signing is pluggable and dependency-free by default.** The default signer is OpenSSH's own
  signature mode (``ssh-keygen -Y``), which needs no extra package and reuses a key you already
  have. ``--signer`` / ``--verifier`` accept any external tool (minisign, age, cosign, …).

Signature material travels with the pack: ``checksums.txt.sig`` (the detached signature) and
``signature.json`` (the algorithm, namespace, signer identity, and the public key + its
fingerprint), so a recipient can verify offline.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cadora.report import _sha256, write_report

NAMESPACE = "cadora-evidence"
CHECKSUMS = "checksums.txt"
SIGNATURE = "checksums.txt.sig"
SIGNATURE_META = "signature.json"


@dataclass
class VerifyResult:
    hashes_ok: bool
    checked: int
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    signature: str = "absent"  # "absent" | "valid" | "invalid" | "unverified"
    signer: str | None = None
    fingerprint: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        # A pack is "ok" when its hashes hold and — if a signature is present — it verifies.
        return self.hashes_ok and self.signature in {"absent", "valid"}


def _pack_dir(archive_dir: str | Path, run_id: str) -> Path:
    return Path(archive_dir) / run_id / "report"


def ensure_pack(archive_dir: str | Path, run_id: str) -> Path:
    """Return the pack's ``checksums.txt``, building the pack first if it isn't there yet."""
    checksums = _pack_dir(archive_dir, run_id) / CHECKSUMS
    if not checksums.is_file():
        write_report(archive_dir, run_id)
    return checksums


def sign_pack(
    archive_dir: str | Path,
    run_id: str,
    *,
    key: str | None = None,
    signer: str | None = None,
    identity: str | None = None,
) -> dict:
    """Sign a run's evidence pack. Returns metadata about the signature it wrote.

    ``signer`` is an external command template (``{file}`` is the checksums path, and the command
    must leave the detached signature at ``{file}.sig``). Without it, the default OpenSSH signer
    is used and ``key`` (a private key path) is required.
    """
    checksums = ensure_pack(archive_dir, run_id)
    sig_path = checksums.with_name(SIGNATURE)

    if signer:
        meta = _run_signer(signer, checksums, sig_path)
    else:
        if not key:
            raise SystemExit("signing needs a key: pass --key <ssh private key> (or --signer <cmd>)")
        meta = _ssh_sign(checksums, sig_path, key, identity)

    (checksums.with_name(SIGNATURE_META)).write_text(json.dumps(meta, indent=2) + "\n")
    return {**meta, "signature": str(sig_path)}


def verify_pack(
    archive_dir: str | Path,
    run_id: str,
    *,
    allowed_signers: str | None = None,
    verifier: str | None = None,
) -> VerifyResult:
    """Verify a run's evidence pack: recompute every hash, then check any signature."""
    run_dir = Path(archive_dir) / run_id
    checksums = run_dir / "report" / CHECKSUMS
    if not checksums.is_file():
        raise FileNotFoundError(f"no evidence pack at {checksums} — run `cadora report {run_id}` first")

    result = _check_hashes(run_dir, checksums)

    sig_path = checksums.with_name(SIGNATURE)
    if not sig_path.is_file():
        result.detail = "no signature (checksummed, not signed)"
        return result

    meta = _read_meta(checksums.with_name(SIGNATURE_META))
    result.signer = (meta or {}).get("identity")
    result.fingerprint = (meta or {}).get("fingerprint")
    if verifier:
        ok, detail = _run_verifier(verifier, checksums, sig_path)
    else:
        ok, detail = _ssh_verify(checksums, sig_path, meta, allowed_signers)
    result.signature = "valid" if ok else "invalid"
    result.detail = detail
    return result


# --- hash floor (always-on, no dependency) -----------------------------------------------------


def _check_hashes(run_dir: Path, checksums: Path) -> VerifyResult:
    mismatched: list[str] = []
    missing: list[str] = []
    checked = 0
    for line in checksums.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _, rel = line.partition("  ")
        target = run_dir / rel
        if not target.is_file():
            missing.append(rel)
            continue
        checked += 1
        if _sha256(target) != expected:
            mismatched.append(rel)
    return VerifyResult(
        hashes_ok=not mismatched and not missing,
        checked=checked,
        mismatched=mismatched,
        missing=missing,
    )


# --- OpenSSH signer (default) ------------------------------------------------------------------


def _ssh_sign(checksums: Path, sig_path: Path, key: str, identity: str | None) -> dict:
    key_path = str(Path(key).expanduser())
    _run(
        ["ssh-keygen", "-Y", "sign", "-f", key_path, "-n", NAMESPACE, str(checksums)],
        "ssh-keygen sign",
    )
    produced = checksums.with_name(checksums.name + ".sig")
    if produced != sig_path:
        produced.replace(sig_path)
    public_key = _run(["ssh-keygen", "-y", "-f", key_path], "ssh-keygen pubkey").strip()
    return {
        "tool": "ssh-keygen",
        "namespace": NAMESPACE,
        "identity": identity or "cadora-pack-signer",
        "public_key": public_key,
        "fingerprint": _fingerprint(public_key),
    }


def _ssh_verify(checksums: Path, sig_path: Path, meta: dict | None, allowed_signers: str | None):
    identity = (meta or {}).get("identity") or "cadora-pack-signer"
    tmp_allowed: Path | None = None
    if allowed_signers:
        allowed_path = str(Path(allowed_signers).expanduser())
        trust = "authenticated against your allowed-signers file"
    else:
        # Self-attesting: verify the signature against the public key that travels in the pack.
        # This proves the pack is unchanged since it was signed by the holder of that key — but the
        # key is trusted on first use, so report its fingerprint for out-of-band confirmation.
        pub = (meta or {}).get("public_key")
        if not pub:
            return False, "signature present but no public key recorded to verify it against"
        tmp_allowed = sig_path.with_name("._allowed_signers")
        tmp_allowed.write_text(f"{identity} {pub}\n")
        allowed_path = str(tmp_allowed)
        trust = f"self-attested — confirm signer key {(meta or {}).get('fingerprint')} out of band"
    try:
        proc = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", allowed_path, "-I", identity,
             "-n", NAMESPACE, "-s", str(sig_path)],
            stdin=checksums.open("rb"), capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False, "ssh-keygen not found — cannot verify the signature"
    finally:
        if tmp_allowed and tmp_allowed.exists():
            tmp_allowed.unlink()
    if proc.returncode == 0:
        return True, trust
    return False, (proc.stderr or proc.stdout).strip()[-300:] or "signature did not verify"


# --- pluggable external signer / verifier ------------------------------------------------------


def _run_signer(signer: str, checksums: Path, sig_path: Path) -> dict:
    cmd = _expand(signer, checksums)
    _run(cmd, "external signer", shell_join=signer)
    if not sig_path.is_file():
        raise SystemExit(f"signer did not produce {sig_path.name}: {signer!r}")
    return {"tool": "external", "namespace": NAMESPACE, "identity": "external-signer",
            "command": signer, "public_key": None, "fingerprint": None}


def _run_verifier(verifier: str, checksums: Path, sig_path: Path):
    cmd = _expand(verifier, checksums, sig_path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        return False, f"verifier not found: {exc}"
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-300:]


def _expand(template: str, checksums: Path, sig_path: Path | None = None) -> list[str]:
    parts = shlex.split(template)
    return [
        p.replace("{file}", str(checksums)).replace("{sig}", str(sig_path or checksums.with_name(SIGNATURE)))
        for p in parts
    ]


# --- small helpers -----------------------------------------------------------------------------


def _run(cmd: list[str], what: str, shell_join: str | None = None) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit(f"{cmd[0]} not found — required for {what}") from None
    if proc.returncode != 0:
        raise SystemExit(f"{what} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    return proc.stdout


def _fingerprint(public_key: str) -> str | None:
    tmp = Path.home() / ".cache" / "cadora"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        keyfile = tmp / "_pub_for_fp"
        keyfile.write_text(public_key + "\n")
        out = subprocess.run(["ssh-keygen", "-lf", str(keyfile)], capture_output=True, text=True)
        keyfile.unlink(missing_ok=True)
        if out.returncode == 0:
            return out.stdout.split()[1] if len(out.stdout.split()) > 1 else out.stdout.strip()
    except (OSError, FileNotFoundError):
        return None
    return None


def _read_meta(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
