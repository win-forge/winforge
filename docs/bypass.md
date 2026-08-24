# Win11 System-Requirement Bypass

Skip the TPM 2.0 / Secure Boot / 4 GB RAM / CPU compatibility gate that
otherwise blocks clean installs of Windows 11 on unsupported hardware.

Two layers, both shipped to every Win11 product by default:

1. **Registry tweak** via autounattend `windowsPE` pass — always on for Win11.
2. **DLL patch** in `install.wim` — only when bypass DLLs are provided as a
   private-repo secret.

## Layer 1: registry tweak (always on)

`autounattend/base.xml` writes three `LabConfig` DWORDs into
`HKLM\SYSTEM\Setup\LabConfig` before the appraiser compatibility check
runs:

```
BypassTPMCheck         = 1
BypassSecureBootCheck  = 1
BypassRAMCheck         = 1
```

Verified working for **21H2 → 23H2** across all SKUs. For **24H2+** it
usually still works on Home/Pro, but Microsoft has been seen to block it
on some Enterprise SKUs. The DLL patch (layer 2) covers that.

## Layer 2: DLL patch (opt-in, version-locked)

`scripts/build/bypass_win11_requirements.py` mounts `install.wim` and
replaces:

```
Windows/System32/appraiserres.dll
Windows/System32/appraiser.dll
```

with patched versions that return "compatible" for any hardware.

### Source DLLs

The DLLs are version-specific and are **vendored directly in this repo**
at `bypass/<product>/` (this replaces an earlier private-repo +
base64-secret design — see the decision record in
[`bypass/README.md`](../bypass/README.md)):

```
bypass/
  win11-24h2/
    appraiserres.dll
    appraiser.dll
  win11-25h2/
    appraiserres.dll
    appraiser.dll
```

You can grab them from any community source (e.g.
[AveYo/MediaCreationTool](https://github.com/AveYo/MediaCreationTool)'s
`Skip_TPM_Check_on_Dynamic_Update.cmd` fetches them, or use the
`bypass11/` directory directly from a release tarball). The pair must be
version-matched to the target build — see the locking notes in
[`bypass/README.md`](../bypass/README.md).

### Wiring

No secrets or extra setup: drop the two DLLs into
`bypass/<product>/` in this repo and commit. At build time the
"Check Win11 bypass policy" step (`scripts/build/bypass_policy.py`) reads
the `needs_dll_bypass` flag for the product+edition from
`config/editions.yaml`:

- flag not set → DLL steps skipped entirely (registry tweak only)
- flag set + DLLs present → staged into `artifacts/bypass/` and patched
  into the WIM
- flag set + DLLs missing → build **fails fast** with a pointer to
  `bypass/README.md`

## How the build flow works

```
convert.sh          # UUP -> ISO
wimlib-imagex       # inject Intel RST drivers (build.yml step, Linux)
bypass_win11_requirements.py   # DLL patch (optional) + commit WIM
repack.sh           # install.wim + autounattend.xml -> final ISO
                     # autounattend carries the LabConfig registry keys
```

## Testing

```bash
pytest tests/test_bypass_win11.py tests/test_autounattend_bypass.py -q
```

The bypass script is fully mocked. The autounattend test parses
`autounattend/base.xml` and asserts the three LabConfig keys are present
in the `windowsPE` pass.

## Verification on real hardware

After installing a built ISO on a TPM-less / 4 GB RAM / no-Secure-Boot
machine, you should see the install proceed straight to the partition
screen with no "This PC can't run Windows 11" dialog. If you see the
dialog, the registry tweak got stripped — check that the
`autounattend/base.xml` `<RunSynchronousCommand>` block made it into the
final ISO's `autounattend.xml`.
