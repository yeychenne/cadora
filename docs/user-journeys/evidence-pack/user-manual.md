# The evidence pack — user manual

Cadora runs prove their result rather than assert it: every gate re-runs the real build, tests,
or scan and archives the verdict. The **evidence pack** is how that proof leaves your machine —
a small, portable bundle that anyone can check, offline, with no Cadora installed. This manual is
for the person **producing** a pack and the person **verifying** one on the other side.

---

## 1. What the pack is, and why

When a run finishes, `cadora report <run-id>` writes a self-contained pack under
`runs/<id>/report/`:

| File | What it is |
|---|---|
| `report.html` | The human view — nodes, gate and integrity verdicts, per-node cost and duration, and the human-review trail. Self-contained: no external fonts, scripts, or images. |
| `report.json` | The same evidence, structured for machines. |
| `checksums.txt` | The SHA-256 of **every** archived run file plus `report.json`, one line per file. |

That last file is the whole point. It makes the pack **tamper-evident**: recompute the hashes and
any change — one edited byte, one dropped file — shows up as a mismatch. `cadora sign` then adds the
missing half, a detached signature over `checksums.txt`, which makes a verified pack **attributable**
as well: proof both that nothing changed *and* who stood behind it.

> **Honesty contract.** The pack claims exactly what the archive recorded — deterministic gate
> verdicts, integrity findings, human-review decisions, and per-node cost (real, or price-table
> estimates explicitly marked *est.*). It does not claim the agents were honest beyond what the
> gates and integrity checks verified.

---

## 2. Produce a pack — `cadora report`

```bash
cadora report pr1-verify --archive-dir runs
```

```
evidence pack for pr1-verify:
  html      runs/pr1-verify/report/report.html
  json      runs/pr1-verify/report/report.json
  checksums runs/pr1-verify/report/checksums.txt
  verify:   cd runs/pr1-verify && shasum -a 256 -c …/report/checksums.txt
```

The pack lands next to the run by default. To put it elsewhere (for example, a directory you'll zip
and send), pass `--out`:

```bash
cadora report pr1-verify --archive-dir runs --out ./pr1-verify-pack
```

Re-running `report` regenerates the pack from the current archive. If you signed a pack and then
re-report, re-sign it — the signature is over the old `checksums.txt`.

---

## 3. Verify the checksums by hand

You don't need Cadora to check a pack's integrity — `checksums.txt` is in the standard format
`shasum`/`sha256sum` read. From the **run directory** (the paths in the file are relative to it):

```bash
cd runs/pr1-verify
shasum -a 256 -c report/checksums.txt
```

```
requirements/output.txt: OK
requirements/integrity.json: OK
design/aidlc-docs/audit.md: OK
report/report.json: OK
...
```

If a file was changed, that line reads `FAILED` and `shasum` exits non-zero:

```
design/aidlc-docs/audit.md: FAILED
shasum: WARNING: 1 computed checksum did NOT match
```

`sha256sum -c report/checksums.txt` works identically on Linux. A pack that passes this check is
unchanged since it was packed — that's the tamper-evident floor, available to anyone.

---

## 4. Sign a pack — `cadora sign`

Signing binds the pack to a key. The default signer is OpenSSH's own signature mode
(`ssh-keygen -Y sign`) — no extra dependency, and it reuses a key you may already have.

**Make a signing key once** (keep the private key private):

```bash
ssh-keygen -t ed25519 -f pack-key -N ""
```

**Sign the pack:**

```bash
cadora sign pr1-verify --archive-dir runs --key pack-key --identity yves@cadora.dev
```

```
signed evidence pack for pr1-verify:
  signature  runs/pr1-verify/report/checksums.txt.sig
  signer     ssh-keygen · yves@cadora.dev
  key        SHA256:u4yCokMkI9o7tsmwDYVGM9WBBxkyz8uF53e8y9HJJx8
  verify:    cadora verify pr1-verify --archive-dir runs
```

`sign` builds the pack first if you haven't run `report` yet, so one command is enough. It adds two
files beside the pack:

- `checksums.txt.sig` — the detached signature over `checksums.txt`.
- `signature.json` — the algorithm, namespace, signer identity, and the **public key plus its
  fingerprint**, so a recipient can verify entirely offline.

`--identity` is the label recorded in the verdict (use something recognizable, like your email).
The **fingerprint** (`SHA256:…`) is the key's identity — note it down and share it through a
channel the recipient already trusts (see §5, trust-on-first-use).

> Prefer a different signing tool (minisign, cosign, age)? Pass `--signer '<cmd>'` where `{file}`
> is the checksums path and the command must leave the detached signature at `{sig}`.

---

## 5. Verify a pack — `cadora verify`

```bash
cadora verify pr1-verify --archive-dir runs
```

`verify` recomputes every hash in `checksums.txt`, then checks the signature (if the pack has one).
The verdict is three lines:

```
evidence pack pr1-verify:
  hashes    23 file(s) OK
  signature VALID — yves@cadora.dev · self-attested — confirm signer key SHA256:u4yCokMkI9o7tsmwDYVGM9WBBxkyz8uF53e8y9HJJx8 out of band
  => VERIFIED
```

Read it top to bottom:

- **`hashes … file(s) OK`** — the tamper-evident floor. Green means every archived file and
  `report.json` matches. A failure reads `hashes MISMATCH: <paths>` (or `<path> (missing)`).
- **`signature …`** — `VALID`, `INVALID`, or `none (checksummed, not signed)`, plus the signer and
  the **trust basis**.
- **`=> VERIFIED`** — the bottom line. It's `VERIFIED` (exit code `0`) only when the hashes hold
  **and** any signature checks out; otherwise `=> NOT VERIFIED` (exit code `1`). An unsigned pack
  that passes its hashes is still `VERIFIED` — integrity without attribution.

### Trust-on-first-use (self-attested)

By default `verify` checks the signature against the public key **that travels in the pack**. That
proves the pack is unchanged since its holder signed it — but on its own it doesn't prove *who* that
holder is, because the key came with the pack. So the verdict prints the fingerprint and tells you to
**confirm the signer key `SHA256:…` out of band** — compare it against a fingerprint the signer gave
you through a trusted channel. Confirm it once; you now trust that key.

To make trust explicit instead of first-use, verify against an OpenSSH `allowed_signers` file you
maintain:

```bash
cadora verify pr1-verify --archive-dir runs --allowed-signers ~/.config/cadora/allowed_signers
```

Each line is `identity ssh-ed25519 AAAA…`. When the signer is authenticated against that file, the
verdict says so instead of `self-attested`.

---

## 6. Share the pack

The pack is self-contained — no server, no database, no live Cadora. To hand it off, send the
`report/` directory (and, if you want the archived source files verifiable too, the run directory
around it — the checksums reference paths relative to the run dir):

```bash
# zip the run so `shasum -c` and `cadora verify` both work on the other side
cd runs && zip -r pr1-verify-pack.zip pr1-verify
```

The recipient unzips, then either runs the by-hand `shasum -a 256 -c report/checksums.txt` (§3) or,
with Cadora installed, `cadora verify pr1-verify --archive-dir .` (§5). `report.html` opens in any
browser and renders identically anywhere.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no evidence pack at … — run cadora report <run> first` | The pack was never built | Run `cadora report <run>` (or `cadora sign`, which builds it first) |
| `hashes MISMATCH: <path>` | A checksummed file changed since packing — tamper, or an edit | Don't trust the pack; get an untampered copy. If **you** edited on purpose, re-run `cadora report` to re-seal |
| `MISMATCH: <path> (missing)` | A checksummed file isn't present | The pack is incomplete — obtain the full run directory, not just `report/` |
| `signature INVALID — <reason>` | The signature doesn't match the checksums or the recorded key — altered after signing, or wrong key | Get a freshly signed pack from the signer |
| `signature none (checksummed, not signed)` | The pack was never signed | Ask the sender to `cadora sign`, or accept checksummed-only (integrity, no attribution) |
| Signer fingerprint is unfamiliar | Trust-on-first-use — the key came with the pack | Confirm `SHA256:…` with the sender out of band, or verify with `--allowed-signers` |
| `ssh-keygen not found` | No OpenSSH on this machine | Install OpenSSH, or sign/verify with another tool via `--signer` / `--verifier` |

---

## 8. Reference

**`cadora report <run-id>`** — build the pack.
`--archive-dir <dir>` · `--out <dir>` (default `<archive-dir>/<run-id>/report/`)

**`cadora sign <run-id>`** — add a detached signature (attributable).
`--key <ssh-private-key>` (required for the default signer) · `--identity <you@example.com>` ·
`--signer '<cmd>'` (external signer; `{file}` = checksums, must write `{sig}`)

**`cadora verify <run-id>`** — recompute hashes, then check any signature.
`--archive-dir <dir>` · `--allowed-signers <file>` (default: self-attest) ·
`--verifier '<cmd>'` (external verifier; `{file}` = checksums, `{sig}` = signature)

**By hand (no Cadora):** from the run directory,
`shasum -a 256 -c report/checksums.txt` (or `sha256sum -c report/checksums.txt`).

**Signature material** (written by `sign`, read by `verify`): `checksums.txt.sig` (detached
signature) and `signature.json` (namespace `cadora-evidence`, signer identity, public key, and
fingerprint). Both travel with the pack for offline verification.
