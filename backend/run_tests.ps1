# 一键本地测试（Windows PowerShell）
# 用法：在 backend 目录下执行  .\run_tests.ps1
# 首次运行会创建 .venv 并安装依赖，之后重复运行只跑测试。

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "== 创建虚拟环境 =="
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

Write-Host "== 安装依赖（首次较慢）=="
pip install -q -r requirements.txt -r requirements-dev.txt

Write-Host "== 运行测试 =="
$env:TESTING = "1"
$env:DATABASE_URL = "sqlite+aiosqlite:///./test_local.db"
$env:GOOGLE_API_KEY = "test-key-local"

pytest -x -q --tb=short
$code = $LASTEXITCODE

Remove-Item -Force test_local.db -ErrorAction SilentlyContinue
if ($code -eq 0) { Write-Host "`n✅ 全部测试通过" } else { Write-Host "`n❌ 有测试失败（exit $code）" }
exit $code
