Unregister-ScheduledTask -TaskName 'GPA_TradingBot_AutoStart' -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument '-WindowStyle Hidden -ExecutionPolicy Bypass -File "D:\GitRepo\GPA_TradingBot\start_trading_bot.ps1"'
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'GPA_TradingBot_AutoStart' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Description 'Auto-start GPA Trading Bot and dashboard on system boot'
