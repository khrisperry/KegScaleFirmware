# Keg Scale firmware downloads

Current coordinated release: **V1.3.4**, September 23, 2026.

This repository distributes OTA application images, channel manifests, and release
notes for the Scale and both display types. Source and developer documentation:

- [Scale](https://github.com/khrisperry/KegScaleESP)
- [E-paper and Wi-Fi Touch Display](https://github.com/khrisperry/KegScaleESPDisplay)
- [V1.3.4 release notes](docs/releases/V1.3.4.md)
- [Published artifact hashes and source commits](docs/releases/V1.3.4-artifacts.json)

## Choose the correct device

| Device | Hardware / target | Production manifest | Dev manifest |
| --- | --- | --- | --- |
| Scale | ESP32-S3 | [Production](firmware/production/esp32s3/manifest.json) | [Dev](firmware/dev/esp32s3/manifest.json) |
| E-paper | LILYGO T5 V2.3.1 / ESP32 | [Production](display/production/esp32/manifest.json) | [Dev](display/dev/esp32/manifest.json) |
| Touch Display | Waveshare ESP32-S3-Touch-LCD-4B / ESP32-S3 | [Production](touchscreen/production/esp32s3/manifest.json) | [Dev](touchscreen/dev/esp32s3/manifest.json) |

Images are not interchangeable. Any legacy target directories are historical;
current Scale firmware supports ESP32-S3. OTA images alone are not first-install
USB bundles: use the source project's matching bootloader/partition layout for
first installation.

## Channels and updates

Production corresponds to each source repository's `main` branch, Dev to `dev`,
and Beta to `beta`. Distribution files for all channels live on this repository's
`main` branch. V1.3.4 updates Production and Dev. Scale and e-paper retain their
older V1.1.0 Beta manifests; a Touch Beta manifest is not currently published and
is tracked in `KegScaleESPDisplay` issue #7.

Use the device's firmware settings to select a channel and check for updates.
Update the Scale first, then e-paper and Touch. E-paper OTA is coordinated by the
Scale over an authenticated BLE bond and may wait for a scheduled wake or restart.
Touch downloads its own update over Wi-Fi. Saved channel preferences are preserved;
Touch settings currently default to Dev when no preference is saved. Choose
Production under Update for release-only updates. Refresh the browser after updating Scale.

V1.3.4 includes phone-friendly Dashboard/Glass views, serving-size vessels,
organized settings, guided independent display pairing, and reliability fixes.
Guided e-paper touch calibration is removed. Default sensitivity is 1%; existing
saved values are preserved. Scale weight calibration is unchanged.

## Publishing

Source pushes do not automatically publish. Local releases use ESP-IDF 6.0.1,
run the matching host/regression checks, and publish only hardware-validated
artifacts from clean sibling source checkouts.

The helper reads the normal ESP-IDF `build` directories, validates the embedded
application name/version/target, calculates SHA-256, writes a commit-named image,
updates the selected channel manifest, and writes a scoped artifact inventory.
Commit and push remain separate.

For a Scale-only Dev OTA after local hardware validation:

```powershell
cd C:\Users\kperry\Documents\GitHub\KegScaleFirmware
git pull origin main
python tools\prepare_release.py --version V1.3.12 --device scale --channel dev
git status
```

That command changes only `firmware/dev/esp32s3` and creates
`docs/releases/V1.3.12-scale-dev-artifacts.json`. It does not modify Production,
Touch, or e-paper manifests. Repeat `--device` or `--channel` when intentionally
publishing multiple targets.

For a coordinated release after all three devices have been validated, omitting
`--device` and `--channel` retains the all-devices/all-channels behavior:

```powershell
python tools\prepare_release.py --version V1.3.12
```

Older images remain available for historical links; manifests identify the current
release. Host tests do not establish physical battery, RF, or power-loss behavior.
