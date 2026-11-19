# F-10: 毎週の自動実行を Windows のタスクスケジューラに登録する。
#   powershell -ExecutionPolicy Bypass -File tools\register_weekly_task.ps1 -Day Monday -Time 18:00
# 解除:  Unregister-ScheduledTask -TaskName AiMentionChecker -Confirm:$false
# 日本時間 17時ごろ (太平洋時間の0時) 以降にすると、その日の無料枠を丸ごと使える。
param(
    [string]$Day = "Monday",
    [string]$Time = "18:00",
    [string]$Config = "config\target.yaml",
    [string]$TaskName = "AiMentionChecker"
)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "仮想環境がありません: $py (README の準備を先に)" }
$log = Join-Path $root "runs\scheduled.log"
$cmd = "/c cd /d `"$root`" && `"$py`" run.py --config `"$Config`" --new --no-capture >>`"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "AI言及チェッカーの毎週の実行" -Force | Out-Null
Write-Host "登録しました: 毎週 $Day $Time。ログ: $log"
