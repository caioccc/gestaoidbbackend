$token = (heroku auth:token).Trim()
$imageId = (docker inspect registry.heroku.com/gestaoidb/web --format="{{.Id}}").Trim()

$headers = @{
    "Authorization" = "Bearer $token"
    "Accept" = "application/vnd.heroku+json; version=3.docker-releases"
    "Content-Type" = "application/json"
}

$body = @{
    "updates" = @(
        @{
            "type" = "web"
            "docker_image" = $imageId
        }
    )
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://api.heroku.com/apps/gestaoidb/formation" -Method Patch -Headers $headers -Body $body