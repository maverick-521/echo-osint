# ECHO remote access from this PC (Cloudflare Tunnel). Gives an https:// link that works on cellular / any Wi-Fi
# while this PC is awake. Everyone except this PC's own ECHO window must enter the PIN from echo.config.json.
#   powershell -ExecutionPolicy Bypass -File deploy\echo-remote.ps1            (quick tunnel: free, no account, link changes each start)
#   set ECHO_CF_TOKEN + ECHO_PUBLIC_URL first for a named tunnel with a fixed hostname (Cloudflare account + domain)
$ErrorActionPreference = "Stop"
$app = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bin = Join-Path $app "bin"; New-Item -ItemType Directory -Force $bin | Out-Null
$cf = Join-Path $bin "cloudflared.exe"
if (-not (Test-Path $cf)) {
  Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf -UseBasicParsing
}
$pyw = Join-Path $app ".venv\Scripts\pythonw.exe"; $server = Join-Path $app "app\server.py"
$url = "http://127.0.0.1:8765/"
function Info { try { (Invoke-WebRequest "$url`api/info" -UseBasicParsing -TimeoutSec 2).Content | ConvertFrom-Json } catch { $null } }
$i = Info
if (-not $i -or $i.bind -ne "tunnel") {
  Get-CimInstance Win32_Process | ? { $_.CommandLine -like "*Programs\ECHO\app\server.py*" } | % { Stop-Process -Id $_.ProcessId -Force }
  Start-Sleep -Milliseconds 800
  $env:ECHO_BIND = "tunnel"; $env:ECHO_NO_BROWSER = "1"
  Start-Process $pyw -ArgumentList "`"$server`"" -WorkingDirectory $app -WindowStyle Hidden
  for ($n = 0; $n -lt 60; $n++) { Start-Sleep -Milliseconds 400; if ((Info).bind -eq "tunnel") { break } }
}
Get-CimInstance Win32_Process | ? { $_.Name -eq "cloudflared.exe" -and $_.CommandLine -like "*127.0.0.1:8765*" } | % { Stop-Process -Id $_.ProcessId -Force }
$log = Join-Path $env:TEMP "echo-tunnel.log"; Remove-Item $log -ErrorAction SilentlyContinue
if ($env:ECHO_CF_TOKEN) {
  Start-Process $cf -ArgumentList "tunnel --no-autoupdate run --token $env:ECHO_CF_TOKEN --url http://127.0.0.1:8765" -WindowStyle Hidden -RedirectStandardError $log
  Set-Content (Join-Path $app ".echo_public_url") $env:ECHO_PUBLIC_URL
  $pub = $env:ECHO_PUBLIC_URL
} else {
  Start-Process $cf -ArgumentList "tunnel --no-autoupdate --url http://127.0.0.1:8765" -WindowStyle Hidden -RedirectStandardError $log
  $pub = $null
  for ($n = 0; $n -lt 60 -and -not $pub; $n++) {
    Start-Sleep 1
    if (Test-Path $log) { $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select -First 1; if ($m) { $pub = $m.Matches[0].Value } }
  }
  if ($pub) { Set-Content (Join-Path $app ".echo_public_url") $pub }
}
if ($pub) { Write-Host "ECHO remote link: $pub  (PIN is in echo.config.json; 'Open on phone' in ECHO shows the link)" } else { Write-Host "Tunnel did not come up - see $log" }
