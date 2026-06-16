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

The DLLs are version-specific and go in the **private repo**, not the
public one. Layout:

```
winforge-private/
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
`bypass11/` directory directly from a release tarball).

### Wiring

Build a tarball of `bypass/<product>/` and base64-encode it:

```bash
cd winforge-private/bypass
tar czf - win11-24h2/ win11-25h2/ | base64 -w0 > bypass-dlls.b64
```

Add as Actions secret `BYPASS_DLLS_B64` on the public repo. The build
workflow auto-extracts into `artifacts/bypass/` and runs the patch step.
If the secret is absent, only the registry tweak is applied.

## How the build flow works

```
convert.sh          # UUP -> ISO
dism-helpers.ps1    # inject Intel RST drivers
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
