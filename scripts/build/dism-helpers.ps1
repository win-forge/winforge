param(
    [Parameter(Mandatory=$true)] [string] $WimPath,
    [Parameter(Mandatory=$true)] [string] $MountDir,
    [Parameter(Mandatory=$true)] [string[]] $DriverDirs,
    [string[]] $AddCapabilityNames = @(),
    [string] $CapabilitySourceDir = ""
)

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
$ErrorActionPreference = "Stop"

Write-Host "[inject] Mounting $WimPath -> $MountDir"
dism /Mount-Wim /WimFile:$WimPath /Index:1 /MountDir:$MountDir

try {
    foreach ($d in $DriverDirs) {
        if (-not (Test-Path $d)) { throw "Driver dir missing: $d" }
        Write-Host "[inject] Adding drivers from $d"
        dism /Image:$MountDir /Add-Driver /Driver:$d /Recurse
    }
    if ($AddCapabilityNames.Count -gt 0 -and $CapabilitySourceDir) {
        foreach ($cap in $AddCapabilityNames) {
            Write-Host "[inject] Adding capability $cap"
            dism /Image:$MountDir /Add-Capability /CapabilityName:$cap /Source:$CapabilitySourceDir
        }
    }
} finally {
    Write-Host "[inject] Unmounting + committing"
    dism /Unmount-Wim /MountDir:$MountDir /Commit
}
