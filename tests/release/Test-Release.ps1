<#
.SYNOPSIS
  Release tests for PostQueue builds. Needs only Windows PowerShell (no Python), so it runs on a clean PC or in
  Windows Sandbox. The app is pointed at a data folder under -Out (POSTQUEUE_DATA), so the tester's own queue and
  settings are not touched; the test settings turn off close-to-tray and start-at-sign-in (the latter marks the
  "PostQueue Startup" entry disabled in StartupApproved; the script removes that mark at the end).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tests\release\Test-Release.ps1 -Dist .\dist -Out .\release-results
#>
param(
  [Parameter(Mandatory)] [string] $Dist,
  [string] $Out = (Join-Path $PWD 'release-results'),
  [switch] $SkipMsi
)
$ErrorActionPreference = 'Stop'
$Dist = (Resolve-Path $Dist).Path; $Out = [IO.Path]::GetFullPath($Out)  # msiexec runs elsewhere, so use full paths
Add-Type -Namespace PQ -Name U -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern System.IntPtr FindWindow(string c, string t);
[DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr h, uint m, System.IntPtr w, System.IntPtr l);
[DllImport("user32.dll")] public static extern bool IsWindow(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool IsIconic(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h, int c);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(System.IntPtr h, out uint pid);
'@
New-Item -ItemType Directory -Force $Out | Out-Null
$data = Join-Path $Out 'data'; New-Item -ItemType Directory -Force $data | Out-Null
# setup_done keeps the first-run wizard (a modal dialog) from opening; these tests exercise the window, not the setup steps.
'{"close_to_tray": false, "start_with_windows": false, "setup_done": true}' | Set-Content -Encoding ascii (Join-Path $data 'settings.json')
$env:POSTQUEUE_DATA = $data; $lock = Join-Path $data 'postqueue.lock'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'; $runValue = 'PostQueue Startup'
$approvedKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
$results = [System.Collections.Generic.List[object]]::new()
function Check($build, $name, $ok, $detail = '') {
  $results.Add([pscustomobject]@{ Build = $build; Test = $name; Result = $(if ($ok) { 'PASS' } else { 'FAIL' }); Detail = $detail })
  Write-Host ("{0}  {1,-24} {2} {3}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $build, $name, $detail)
}
function WaitWindow($title, $seconds = 40) {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) { $h = [PQ.U]::FindWindow([NullString]::Value, $title); if ($h -ne [IntPtr]::Zero -and [PQ.U]::IsWindowVisible($h)) { return $h }; Start-Sleep -Milliseconds 300 }
  return [IntPtr]::Zero
}
function WindowPid($h) { $p = 0; [void][PQ.U]::GetWindowThreadProcessId($h, [ref]$p); return [int]$p }
function WaitGone($procId, $seconds = 15) {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) { if (-not (Get-Process -Id $procId -ErrorAction SilentlyContinue)) { return $true }; Start-Sleep -Milliseconds 300 }
  return $false
}
function Machine($file) {
  $b = [IO.File]::ReadAllBytes($file); $pe = [BitConverter]::ToInt32($b, 0x3C); '{0:X4}' -f [BitConverter]::ToUInt16($b, $pe + 4)
}
function LaunchTests($build, $exe, $title) {
  if (Test-Path $lock) { Remove-Item $lock -Force }
  $t0 = Get-Date; Start-Process $exe | Out-Null; $h = WaitWindow $title
  Check $build 'Starts and shows its window' ($h -ne [IntPtr]::Zero) ("{0:N1} s" -f ((Get-Date) - $t0).TotalSeconds)
  if ($h -eq [IntPtr]::Zero) { return }
  $appPid = WindowPid $h; $info = Get-Content $lock -Raw | ConvertFrom-Json
  Check $build 'Lock records window process' ($info.pid -eq $appPid -and $info.started)
  Check $build 'Queue and log files created' ((Test-Path (Join-Path $data 'postqueue.log')) -and (Test-Path (Join-Path $data 'media')))
  [void][PQ.U]::ShowWindow($h, 6); Start-Sleep -Milliseconds 500
  $second = Start-Process $exe -PassThru; $exited = $second.WaitForExit(60000); Start-Sleep -Milliseconds 2500
  Check $build 'Second launch restores open window' ($exited -and -not [PQ.U]::IsIconic($h) -and (Get-Process -Id $appPid -ErrorAction SilentlyContinue))
  [void][PQ.U]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero)
  Check $build 'Clean close removes lock' ((WaitGone $appPid) -and -not (Test-Path $lock))
  if (Get-Process -Id $appPid -ErrorAction SilentlyContinue) { Stop-Process -Id $appPid -Force; [void](WaitGone $appPid); Remove-Item $lock -Force -ErrorAction SilentlyContinue }   # keep later checks meaningful
  Start-Process $exe | Out-Null; $h = WaitWindow $title; $appPid = WindowPid $h
  Stop-Process -Id $appPid -Force; [void](WaitGone $appPid)
  Check $build 'Crash leaves lock behind' (Test-Path $lock)
  Start-Process $exe | Out-Null; $h = WaitWindow $title
  $ok = $h -ne [IntPtr]::Zero -and ((Get-Content $lock -Raw | ConvertFrom-Json).pid -eq (WindowPid $h))
  Check $build 'Relaunch after crash takes over' $ok
  if ($h -ne [IntPtr]::Zero) { $appPid = WindowPid $h; [void][PQ.U]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero); [void](WaitGone $appPid) }
}

$version = $null
foreach ($exe in Get-ChildItem $Dist -Filter 'PostQueue-*.exe') {
  if ($exe.Name -notmatch '^PostQueue-(?<v>[\d.]+)-(?<a>x64|arm64)\.exe$') { continue }
  $version = $Matches.v; $arch = $Matches.a; $title = "PostQueue $version"
  $native = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
  $expected = @{ x64 = '8664'; arm64 = 'AA64' }[$arch]
  foreach ($build in @(@{ n = "$arch portable"; f = $exe.FullName }, @{ n = "$arch one-folder"; f = (Join-Path $Dist "PostQueue-$version-$arch\PostQueue-$version-$arch.exe") })) {
    if (-not (Test-Path $build.f)) { Check $build.n 'Build present' $false $build.f; continue }
    Check $build.n 'CPU type matches name' ((Machine $build.f) -eq $expected) (Machine $build.f)
    $hashFile = "$($exe.FullName).sha256.txt"
    if ($build.n -like '*portable' -and (Test-Path $hashFile)) {
      $recorded = ((Get-Content $hashFile)[1] -replace '\s', '').ToUpper()
      Check $build.n 'Matches recorded SHA-256' ((Get-FileHash $build.f).Hash -eq $recorded)
    }
    $sig = Get-AuthenticodeSignature $build.f
    Check $build.n 'Signature (informational)' $true $sig.Status
    if ($arch -eq 'arm64' -and $native -ne 'arm64') { Check $build.n 'Launch tests' $true 'skipped: ARM64 build on an x64 PC'; continue }
    LaunchTests $build.n $build.f $title
    if ($build.n -like '*one-folder') {
      $notices = Join-Path (Split-Path $build.f) '_internal\THIRD_PARTY_NOTICES.txt'
      Check $build.n 'Carries third-party notices' ((Test-Path $notices) -and (Test-Path (Join-Path (Split-Path $build.f) '_internal\licenses\LGPL-3.0.txt')))
      # A folder name with accented and Chinese characters (built from code points: this file is read as ANSI)
      $unicode = Join-Path $Out ('T' + [char]0xE9 + 'st ' + [char]0xFC + ' ' + [char]0x6D4B + [char]0x8BD5)
      Remove-Item -Recurse -Force $unicode -ErrorAction SilentlyContinue
      Copy-Item (Split-Path $build.f) $unicode -Recurse
      Start-Process (Join-Path $unicode (Split-Path $build.f -Leaf)) | Out-Null; $h = WaitWindow $title
      Check $build.n 'Starts from a non-English folder name' ($h -ne [IntPtr]::Zero)
      if ($h -ne [IntPtr]::Zero) { $appPid = WindowPid $h; [void][PQ.U]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero); [void](WaitGone $appPid) }
      Remove-Item -Recurse -Force $unicode -ErrorAction SilentlyContinue
    }
  }
  $msi = Join-Path $Dist "PostQueue-$version-$arch.msi"
  if (-not $SkipMsi -and (Test-Path $msi) -and -not ($arch -eq 'arm64' -and $native -ne 'arm64')) {
    $n = "$arch MSI (per-user)"
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /l*v `"$Out\msi-install-$arch.log`"" -Wait -PassThru
    $installed = Join-Path $env:LOCALAPPDATA "Programs\PostQueue\PostQueue-$version-$arch.exe"
    Check $n 'Installs without administrator rights' ($p.ExitCode -eq 0 -and (Test-Path $installed)) "exit $($p.ExitCode)"
    Check $n 'Start menu shortcut' (Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\PostQueue.lnk'))
    $run = (Get-ItemProperty $runKey -ErrorAction SilentlyContinue).$runValue
    Check $n 'Start-at-sign-in entry points at it' ($run -eq "`"$installed`" --minimized") $run
    if (Test-Path $installed) { LaunchTests $n $installed $title }
    $run = (Get-ItemProperty $runKey -ErrorAction SilentlyContinue).$runValue
    $state = (Get-ItemProperty $approvedKey -ErrorAction SilentlyContinue).$runValue
    Check $n 'App turned it off without deleting it' ($run -and $state -and $state[0] -eq 3)
    $p = Start-Process msiexec.exe -ArgumentList "/x `"$msi`" /qn" -Wait -PassThru
    $run = (Get-ItemProperty $runKey -ErrorAction SilentlyContinue).$runValue
    Check $n 'Uninstalls cleanly' ($p.ExitCode -eq 0 -and -not (Test-Path (Split-Path $installed)) -and -not $run) "exit $($p.ExitCode)"
    Remove-ItemProperty -Path $approvedKey -Name $runValue -ErrorAction SilentlyContinue
  }
}
$failed = @($results | Where-Object Result -eq 'FAIL').Count
$md = @("# Release test results", "", "Windows $([Environment]::OSVersion.Version), $env:PROCESSOR_ARCHITECTURE, $(Get-Date -Format 'yyyy-MM-dd HH:mm'), version $version", "",
        "| Build | Test | Result | Detail |", "|---|---|---|---|") + ($results | ForEach-Object { "| $($_.Build) | $($_.Test) | $($_.Result) | $($_.Detail) |" }) +
      @("", "**$(@($results).Count - $failed) passed, $failed failed.**")
$md | Set-Content -Encoding utf8 (Join-Path $Out 'results.md')
$results | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Out 'results.json')
Write-Host "$(@($results).Count - $failed) passed, $failed failed. Results: $Out"
exit $failed
