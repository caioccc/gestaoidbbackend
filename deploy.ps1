Write-Host "1. Autenticando Docker no Heroku..." -ForegroundColor Cyan
heroku container:login

Write-Host "2. Limpando pycache local..." -ForegroundColor Cyan
Get-ChildItem -Path . -Recurse -Include __pycache__,*.pyc | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "3. Compilando imagem Docker..." -ForegroundColor Cyan
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
docker build --no-cache --build-arg TIMESTAMP=$timestamp -t registry.heroku.com/gestaoidb/web:latest .

Write-Host "4. Enviando imagem para o Heroku..." -ForegroundColor Cyan
docker push registry.heroku.com/gestaoidb/web:latest

Write-Host "5. Obtendo Image ID e executando Release via API..." -ForegroundColor Cyan
# Captura o Image ID exato que acabou de ser gerado localmente
$imageId = (docker inspect registry.heroku.com/gestaoidb/web:latest --format '{{.Id}}').Replace('sha256:', '')
$apiKey = (heroku auth:token).Trim()

$headers = @{
    "Authorization" = "Bearer $apiKey"
    "Accept"        = "application/vnd.heroku+json; version=3.docker-releases"
    "Content-Type"  = "application/json"
}

$body = @{
    updates = @(
        @{
            type   = "web"
            docker_image = $imageId
        }
    )
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Uri "https://api.heroku.com/apps/gestaoidb/formation" -Method Patch -Headers $headers -Body $body

Write-Host "6. Verificando versao ativa..." -ForegroundColor Cyan
heroku releases -a gestaoidb -n 3