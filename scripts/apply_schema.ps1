# Create KCA + KAMIS + FIS tables on the local Docker MySQL.
# Requires: docker compose up, .env MYSQL_* (host port 3307).
# Re-running drops empty/old tables. Do not run after real load data exists.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .\.env)) {
  Copy-Item .\.env.example .\.env
  Write-Host "Created .env from .env.example. Edit passwords if needed."
}

Get-Content .\.env | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("Env:" + $matches[1].Trim()) -Value $matches[2].Trim().Trim('"').Trim("'")
  }
}

cmd /c "docker compose up -d mysql"

$ready = $false
foreach ($i in 1..30) {
  cmd /c "docker exec cost-radar-mysql mysqladmin ping -h 127.0.0.1 -uroot -p$env:MYSQL_ROOT_PASSWORD --silent >nul 2>&1"
  if ($LASTEXITCODE -eq 0) {
    $ready = $true
    break
  }
  Start-Sleep -Seconds 2
}
if (-not $ready) {
  throw "MySQL did not become ready. Check: docker compose logs mysql"
}

docker cp .\backend\sql\001_kca_schema.sql cost-radar-mysql:/tmp/001_kca_schema.sql
docker cp .\backend\sql\002_kamis_fis_schema.sql cost-radar-mysql:/tmp/002_kamis_fis_schema.sql
docker exec cost-radar-mysql sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" < /tmp/001_kca_schema.sql'
docker exec cost-radar-mysql sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" < /tmp/002_kamis_fis_schema.sql'
docker exec cost-radar-mysql sh -c "mysql -u`$MYSQL_USER -p`$MYSQL_PASSWORD `$MYSQL_DATABASE -e 'SHOW TABLES;'"

Write-Host "Schema ready. 11 tables expected (KCA 7 + KAMIS 2 + FIS 2)."
