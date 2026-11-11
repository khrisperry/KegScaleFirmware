# November 11 diagnostic Dev publication

Run from the Windows ESP-IDF v6.0.1 PowerShell terminal on the computer holding
`~/.kegscale/ota-keys/ota-dev-private.pem`. The private key stays on that computer.

```powershell
cd C:\Users\kperry\Documents\GitHub\KegScaleFirmware
git pull --ff-only origin main
python tools\publish_diagnostics_dev.py --publish
```

The publisher builds and signs these exact tested source commits in detached,
temporary worktrees, without switching the user's source branches:

| Feed | Version | Source commit |
|---|---|---|
| firmware/dev/esp32s3 | V1.4.21 | 0688fd231ab36fb3fdbafbb44d4345d4e03be104 |
| display/dev/esp32 | V1.4.8 | ed80906461e84d97391ad4880269d79e7d671d31 |
| touchscreen/dev/esp32s3 (square 4B only) | V1.4.25 | ed80906461e84d97391ad4880269d79e7d671d31 |

It requires the expected diagnostic branch heads, checks each image's identity,
validates source provenance and signatures, then stages exactly twelve Dev
release files and publishes one commit. Beta and Production are outside its scope.

Because Windows builds can differ from the GitHub binaries, the exact ELF/map
and sdkconfig are saved under `Documents\KegLogs\symbols\<commit>\<device>\<binary-sha256>`
before temporary worktrees are removed. `BUILD_IDENTITY.json` ties symbols to
the signed image hash. Keep those files for later diagnostic-log decoding.
Use `--symbols-dir` to choose a different location.

The 4.3-inch build deliberately disables OTA and cannot use the square 4B feed.
TouchPour remains a separate supervised manual installation, with no automatic
Dev OTA feed. This publisher never stages either hardware's binary.

Successful publication prints `PASS: All three signed Dev feeds published atomically.`
Publishing does not establish that any physical device has installed an update.
