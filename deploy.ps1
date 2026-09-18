Write-Host "1. Autenticando Docker no Heroku..." -ForegroundColor Cyan
heroku container:login

Write-Host "2. Limpando pycache local..." -ForegroundColor Cyan
Get-ChildItem -Path . -Recurse -Include __pycache__,*.pyc | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "3. Compilando imagem Docker..." -ForegroundColor Cyan
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker build --no-cache --build-arg TIMESTAMP=$timestamp -t registry.heroku.com/gestaoidb/web:latest .

Write-Host "4. Enviando imagem para o Heroku..." -ForegroundColor Cyan
docker push registry.heroku.com/gestaoidb/web:latest

Write-Host "5. Aplicando Release..." -ForegroundColor Cyan
heroku container:release web -a gestaoidb

Write-Host "6. Verificando versao..." -ForegroundColor Cyan
heroku releases -a gestaoidb -n 3