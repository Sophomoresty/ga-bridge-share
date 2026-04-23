param(
    [string]$Distro = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Resolved = (Resolve-Path $ScriptDir).Path
$Drive = $Resolved.Substring(0, 1).ToLower()
$Rest = $Resolved.Substring(2) -replace "\\", "/"
$WslPath = "/mnt/$Drive$Rest"
$TargetDistro = if ($Distro) { $Distro } else { "" }

$InstallCmd = "cd '$WslPath' && chmod +x ./install.sh && ./install.sh"

if ($TargetDistro) {
    & wsl.exe -d $TargetDistro bash -lc $InstallCmd
} else {
    & wsl.exe bash -lc $InstallCmd
}
