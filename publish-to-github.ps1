# Publica um novo repositorio PRIVADO na conta informada, mediante autenticacao interativa no GitHub CLI.
# Requisitos: Git, GitHub CLI (gh), autenticacao previa: gh auth login
param(
    [string]$Owner = 'pablo-marchina',
    [string]$Repository = 'hackathon-intelligence-platform'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git nao encontrado. Instale Git for Windows antes de continuar.'
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI nao encontrado. Instale com: winget install --id GitHub.cli'
}

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Autentique-se primeiro executando: gh auth login'
}

$fullName = "$Owner/$Repository"
& gh repo view $fullName --json name *> $null
if ($LASTEXITCODE -eq 0) {
    throw "Ja existe um repositorio com o nome $fullName. Nenhum arquivo foi sobrescrito."
}

$existingRemote = & git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $existingRemote) {
    throw "Este projeto ja tem um remote origin: $existingRemote. Revise antes de publicar."
}

Write-Host "Criando repositorio PRIVADO: $fullName"
& gh repo create $fullName --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) {
    throw 'Publicacao falhou. O repositorio local permanece inalterado.'
}

& gh repo view $fullName --json url,visibility
Write-Host "Publicacao concluida: https://github.com/$fullName"
