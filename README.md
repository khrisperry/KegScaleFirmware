# Keg Scale firmware downloads

Current coordinated release: **V1.4.0**, September 25, 2026.

This repository distributes OTA application images, channel manifests, and release
notes for the Scale and both display types. Source and developer documentation:

- [Scale](https://github.com/khrisperry/KegScaleESP)
- [E-paper and Wi-Fi Touch Display](https://github.com/khrisperry/KegScaleESPDisplay)
- [V1.4.0 coordinated release notes](docs/releases/V1.4.0.md)
- [Scale V1.4.0 artifact inventory](docs/releases/V1.4.0-scale-dev-production-artifacts.json)
- [E-paper V1.4.0 artifact inventory](docs/releases/V1.4.0-display-dev-production-artifacts.json)
- [Touch V1.4.0 artifact inventory](docs/releases/V1.4.0-touchscreen-dev-production-artifacts.json)

## Choose the correct device

| Device | Hardware / target | Production manifest | Beta manifest | Dev manifest |
| --- | --- | --- | --- | --- |
| Scale | ESP32-S3 | [Production](firmware/production/esp32s3/manifest.json) | [Beta](firmware/beta/esp32s3/manifest.json) | [Dev](firmware/dev/esp32s3/manifest.json) |
| E-paper | LILYGO T5 V2.3.1 / ESP32 | [Production](display/production/esp32/manifest.json) | [Beta](display/beta/esp32/manifest.json) | [Dev](display/dev/esp32/manifest.json) |
| Touch Display | Waveshare ESP32-S3-Touch-LCD-4B / ESP32-S3 | [Production](touchscreen/production/esp32s3/manifest.json) | [Beta](touchscreen/beta/esp32s3/manifest.json) | [Dev](touchscreen/dev/esp32s3/manifest.json) |

Images are not interchangeable. Any legacy target directories are historical;
current Scale firmware supports ESP32-S3. OTA images alone are not first-install
USB bundles: use the source project's matching bootloader/partition layout for
first installation.

## Channels and updates

Production, Beta, and Development are supported release channels. Distribution
files for all three channels live on this repository's `main` branch. Production
corresponds to validated source promoted from Dev; Development follows validated
`dev` source checkpoints. **Beta normally mirrors current Production** on all
three devices and is kept ready for future release-candidate testing. Beta uses
the Dev signing trust domain, not the Production private key, so a future Beta
candidate can be staged without granting Production signing authority.

Use the device's firmware settings to select a channel and check for updates.
Update the Scale first, then e-paper and Touch. E-paper OTA is coordinated by the
Scale over an authenticated BLE bond and may wait for a scheduled wake or restart.
Touch downloads its own update over Wi-Fi. Saved channel preferences are preserved;
Touch settings currently default to Dev when no preference is saved. Choose
Production under Update for release-only updates. Refresh the browser after updating Scale.

V1.4.0 is the current coordinated Production baseline for Scale, e-paper, and
Touch. Development firmware may be newer while features and hardening are being
validated. Guided e-paper touch calibration is removed; touch sensitivity remains
manually configurable with a 1% default for new configurations. Scale weight
calibration is unchanged.

## Full regression suite

From PowerShell, one command runs the complete non-destructive regression set
across all three repositories:

```powershell
cd C:\Users\kperry\Documents\GitHub\KegScaleFirmware
python tools\run_all_tests.py
```

The runner expects sibling `KegScaleESP` and `KegScaleESPDisplay` checkouts.
On Windows it uses WSL for the C/C++/Linux host suites and the current Windows
Python for the Playwright web/UI regression plus firmware release/signing checks.
It runs:

- the complete Scale host/guard/crypto regression suite;
- the Scale Playwright browser/layout regression;
- the complete e-paper and Touch host/guard/crypto regression suite, including
  the Touch pairing-overlay regression;
- `release_check.py --source-check` across signed Dev/Beta/Production feeds,
  exact source commits/versions, and docs; and
- the OTA signing/tamper self-test.

Every result line begins with `PASS:` or `FAIL:`. PASS is green and FAIL is
red in an interactive terminal. The runner continues through all suites after an
individual failure so the final summary shows the complete result set. Use
`NO_COLOR=1` or `--no-color` when ANSI colors are not wanted.

## Publishing

Source pushes do not automatically publish. Local releases use ESP-IDF 6.0.1,
run the matching host/regression checks, and publish only hardware-validated
artifacts from clean sibling source checkouts.

The machine-readable release contract is [release_contract.json](release_contract.json).
Run the local checker at any time to validate all published
Dev/Beta/Production manifests, signatures, binary size/SHA/image identity,
supported hardware, protocol ranges, coordinated Production version, Beta's
exact Production mirror, and release-documentation markers:

```powershell
python tools\release_check.py
```

Release-check result lines always begin with `PASS:` or `FAIL:`; interactive
terminals show PASS in green and FAIL in red. Set `NO_COLOR=1` to disable ANSI
color without changing the prefixes.

The checker does not require private signing keys.

The helper reads the normal ESP-IDF `build` directories, validates the embedded
application name/version/target, calculates SHA-256, writes a commit-named image,
updates the selected channel manifest, and writes a scoped artifact inventory.
Commit and push remain separate.

For signed local OTA publishing, install the signing dependency once and generate
the Dev and Production keys on this workstation. Private keys stay outside Git:

```powershell
cd C:\Users\kperry\Documents\GitHub\KegScaleFirmware
python -m pip install cryptography
python tools\ota_signing.py self-test
python tools\ota_signing.py generate --channel dev --channel production --private-dir "$HOME\.kegscale\ota-keys"
```

Commit only the generated `keys/ota-*-public-key.json` files. Never commit the
private PEM files under `$HOME\.kegscale\ota-keys`.

Beta intentionally reuses the **Dev private key** while carrying separate
`keys/ota-beta-public-key.json` metadata whose channel is `beta`. This keeps
Beta outside the Production signing trust domain without adding another private
key to manage.

All currently published manifests are already signed. The
`sign_current_manifests.py` helper remains available for migration/recovery of
existing Dev/Production manifests without rebuilding firmware; normal releases
use `prepare_release.py`, `promote_dev_to_beta.py`,
`promote_dev_to_production.py`, and `sync_beta_to_production.py`.

To reset all Beta streams to the current signed Production baseline:

```powershell
python tools\sync_beta_to_production.py
python tools\release_check.py
git status
```

The Beta sync verifies each Production signature and binary first, copies the
exact Production bytes into the matching Beta path, rewrites only Beta
channel/path/provenance metadata, signs with the Dev private key under the Beta
public-key alias, and verifies the resulting Beta signature.

For a Scale-only Dev OTA after local hardware validation:

```powershell
cd C:\Users\kperry\Documents\GitHub\KegScaleFirmware
git pull origin main
python tools\prepare_release.py --version V1.4.5 --device scale --channel dev --require-signatures
git status
```

`prepare_release.py` now requires a signing key and prepares **Dev only**.
It signs the exact LF-normalized `manifest.json` bytes and writes a sibling
`manifest.sig`. The signature uses ECDSA P-256/SHA-256 and a raw 64-byte
`r || s` value encoded as Base64 in the sidecar JSON. Direct Production
preparation is intentionally blocked.

That command changes only `firmware/dev/esp32s3` and creates
`docs/releases/V1.4.5-scale-dev-artifacts.json`. It does not modify Production,
Touch, or e-paper manifests. Repeat `--device` when intentionally publishing
multiple Dev targets.

After the Dev artifact has passed host tests and hardware validation, promote the
**exact signed Dev binary** to Production without rebuilding it:

```powershell
python tools\promote_dev_to_production.py --version V1.4.5 --device scale
git diff -- firmware\production\esp32s3
git status
```

The promotion helper first verifies the signed Dev manifest, validates the Dev
binary size and SHA-256, requires the candidate version to be newer than the
current Production version, copies those exact bytes into the Production path,
writes Production provenance metadata, signs the Production manifest with the
separate Production key, verifies that signature, and rechecks the Production
binary SHA-256. It never invokes a compiler.

Both `prepare_release.py` and `promote_dev_to_production.py` now invoke
`release_check.py` automatically. Dev preparation verifies the selected source
commit/version and signed artifact. Production promotion additionally proves that
the promoted bytes are identical to the signed Dev artifact and that promotion
provenance is present.

For a coordinated release, prepare and validate each device in Dev first, then
promote each validated device explicitly. Do not regenerate Production artifacts
from source after hardware validation.

After Production is promoted, run `sync_beta_to_production.py` so Beta returns
to the new Production baseline. When Beta is later used for release-candidate
testing, it may intentionally move ahead of Production; the default baseline
policy is to mirror Production when no candidate is under test.

To stage a future signed release candidate from exact Dev bytes:

```powershell
python tools\promote_dev_to_beta.py --version V1.5.0 --device scale --device display --device touchscreen
python tools\release_check.py --channel beta --version V1.5.0 --allow-beta-candidate --source-check
```

That path uses the Dev signing key, never the Production private key. After the
candidate is released to Production, run `sync_beta_to_production.py` again to
reset Beta to the new Production baseline.

Older images remain available for historical links; manifests identify the current
release. Host tests do not establish physical battery, RF, or power-loss behavior.
