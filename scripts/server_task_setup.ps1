# novelcopilot 서버를 작업 스케줄러 태스크로 등록 — cmd 창 없이(숨김) 상시 구동 + 로그온 자동 시작.
# 실행: powershell -ExecutionPolicy Bypass -File scripts\server_task_setup.ps1
# 이후 수동 조작: Start-ScheduledTask / Stop-ScheduledTask -TaskName "novelcopilot-server"

$TaskName = "novelcopilot-server"
$AppDir   = Join-Path (Split-Path -Parent $PSScriptRoot) "app"   # <repo>/app (script location based)
$LogDir   = Join-Path $AppDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }   # logs/ 는 git 제외 — 없으면 리다이렉트가 실패
# 파이썬: app\.venv 가 있으면 그 인터프리터(README 설치 절차), 없으면 전역 py -3.12
$VenvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPy) { $PyCmd = "& '$VenvPy'" } else { $PyCmd = "py -3.12" }

# 1) 기존 :8000 점유 프로세스 정리(세션에 묶인 임시 서버 교체)
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $conn | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Write-Host "기존 서버 종료: PID $_"
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# 2) 태스크 등록 — 숨김 PowerShell이 uvicorn을 품음(콘솔 창 0), 로그는 logs\server_stdout.log 누적
$psArg = "-NoProfile -WindowStyle Hidden -Command `"Set-Location '$AppDir'; $PyCmd -m uvicorn novelcopilot.main:app --host 0.0.0.0 --port 8000 *>> logs\server_stdout.log`""
$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArg
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# ExecutionTimeLimit 0 필수 — 기본값(3일)이면 72시간 뒤 태스크가 서버를 죽인다
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "태스크 등록 완료: $TaskName (로그온 자동 시작·숨김 창·시간 제한 없음)"

# 3) 지금 즉시 시작 + 확인
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 5
    Write-Host "서버 기동 확인: HTTP $($r.StatusCode)"
} catch {
    Write-Host "확인 실패 — logs\server_stdout.log 를 확인하세요: $($_.Exception.Message)"
}
