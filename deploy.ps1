Write-Host "1. Limpando pycache local..." -ForegroundColor Cyan
Get-ChildItem -Path . -Recurse -Include __pycache__,*.pyc | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "2. Compilando imagem Docker..." -ForegroundColor Cyan
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker build --no-cache --build-arg TIMESTAMP=$timestamp -t registry.heroku.com/gestaoidb/web .

Write-Host "3. Enviando para o Heroku..." -ForegroundColor Cyan
docker push registry.heroku.com/gestaoidb/web

Write-Host "4. Aplicando Release..." -ForegroundColor Cyan
.\script_deploy.ps1

Write-Host "5. Verificando versao..." -ForegroundColor Cyan
heroku releases -a gestaoidb -n 3