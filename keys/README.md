# OTA signing public keys

This directory contains only public verification-key metadata. It is safe to
commit and publish.

Private OTA signing keys must remain outside all repositories. The local tools
default to `~/.kegscale/ota-keys` and the repository .gitignore also rejects
common private-key filenames as a second line of defense.

Generate channel keys locally with:

```powershell
python -m pip install cryptography
python tools\ota_signing.py generate --channel dev --channel production --private-dir "$HOME\.kegscale\ota-keys"
```

The command writes:
- private PEM files under the private directory above
- `keys/ota-dev-public-key.json`
- `keys/ota-production-public-key.json`

Beta uses the Dev private key as its non-production trust domain. The committed
`keys/ota-beta-public-key.json` contains the same public key/key ID as Dev but
declares channel `beta`, allowing Beta manifests to remain channel-bound without
creating another private key.

Commit only public JSON metadata. Never commit or upload private PEM files.
