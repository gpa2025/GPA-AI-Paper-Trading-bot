# Start the trading bot dashboard and auto-start trading
$projectDir = "D:\GitRepo\GPA_TradingBot\etrade_python_client"
$python = "D:\GitRepo\GPA_TradingBot\.venv\Scripts\python.exe"

# Launch dashboard in background
Start-Process -FilePath $python -ArgumentList "run_dashboard.py" -WorkingDirectory $projectDir -WindowStyle Hidden

# Wait for dashboard to be ready, then trigger bot start
Start-Sleep -Seconds 5
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/start" -Method Post
