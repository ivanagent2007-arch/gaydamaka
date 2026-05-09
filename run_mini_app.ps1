# Запуск бота с веб-сервером мини-приложения (Windows PowerShell).
# Перед первым запуском: скопируй .env.example -> .env, укажи BOT_TOKEN и HTTPS WEBAPP_PUBLIC_URL.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Write-Host "Нет файла .env. Скопируй .env.example в .env и заполни BOT_TOKEN и WEBAPP_PUBLIC_URL." -ForegroundColor Red
    exit 1
}

$activate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $activate) {
    . $activate
} else {
    Write-Host "Виртуальное окружение .venv не найдено. Выполни:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Мини-приложение: убедись, что WEBAPP_PUBLIC_URL в .env = HTTPS (ngrok и т.д.)." -ForegroundColor Cyan
Write-Host "Подробности: WEBAPP_RUN.txt" -ForegroundColor DarkGray
Write-Host ""

python main.py
