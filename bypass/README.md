# Win11 system-requirement bypass — vendored DLLs

This directory holds the version-paired `appraiserres.dll` and
`appraiser.dll` files that the build's Win11 bypass step drops into
`install.wim` to defeat Microsoft's hardware compatibility check.

## Layout

```
bypass/
  <product>/        e.g. win11-24h2, win11-25h2
    appraiserres.dll
    appraiser.dll
```

`<product>` must match the `${PRODUCT}` env var the build sets. The
build step does:

```bash
cp bypass/${PRODUCT}/{appraiserres,appraiser}.dll artifacts/bypass/
```

If the product directory is empty or missing, the build falls through
to the **registry-tweak-only** path (the `LabConfig` keys in
autounattend). Most SKUs work with registry-only; layer 2 (this) is
needed for some 24H2 Enterprise / 25H2 SKUs where Microsoft blocks
the registry trick.

## Sources

DLLs are version-locked. Wrong pair on the wrong build = either no
bypass (patch loads but version mismatch is rejected) or broken install
(BSOD on first boot with `INACCESSIBLE_BOOT_DEVICE`).

Most common source: [AveYo/MediaCreationTool](https://github.com/AveYo/MediaCreationTool),
specifically the `bypass11/` directory which ships paired DLLs per
build. The repo's release notes / `main` branch list which build each
release targets.

Alternative: extract the original (non-bypass) pair from a Windows 10
22H2 ISO's `install.wim` at `Windows/System32/`. The Win10 pair works
as a no-op bypass for many Win11 24H2 builds (Win11's appraiser checks
against the Win10 signature, which the Win10 DLL "satisfies" by
returning compatible). Less reliable than the community pair.

## Verifying a pair works

After installing a patched ISO on unsupported hardware, confirm:

1. Setup proceeds past "Checking your PC" with no "This PC can't run
   Windows 11" dialog
2. First boot goes straight to OOBE (no BSOD)
3. `msinfo32` shows the OS installed correctly

If install dies on first boot with `INACCESSIBLE_BOOT_DEVICE` or
similar, the patched DLLs have a version mismatch. Re-check that the
source pair matches the target build's major.minor.

## Why vendored, not as a CI secret

This is an explicit decision to keep the bypass DLLs in the repo
rather than as a `BYPASS_DLLS_B64` base64 secret. Trade-off:

- **Pro**: zero setup for new contributors / CI forks; no secret
  rotation; no private-repo handoff.
- **Con**: the public git history now contains Microsoft-signed
  binaries. The skill's original recommendation was to keep these in a
  private repo / base64 secret specifically to avoid this. Decision
  overrules that recommendation — the bypass DLLs are widely mirrored
  on GitHub already, the files are public, and the operational
  complexity of the secret path isn't worth it for this project.
