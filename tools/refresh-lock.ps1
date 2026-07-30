<#
    refresh-lock.ps1 - le verrou du rafraichissement, en un seul endroit testable.

POURQUOI CE FICHIER EXISTE
    Le 29/07, trois build_routes.py ont tourne en parallele : HTTP 429, 50 items sans prix, et le
    run le plus lent a fini APRES la publication en ecrasant une meilleure version de 90 items.
    Un verrou a ete pose dans refresh-hidden.vbs, qui couvre le planificateur, mais un double-clic
    DIRECT sur refresh-data.bat le contournait encore.

POURQUOI PAS EN BATCH
    Tente le 30/07, trois bugs, aucun visible a la relecture :
      1. `if %AGEMIN% LSS 240` dans un bloc `if exist (...)` : en batch, %VAR% d'un bloc parenthese
         est substitue AU PARSING, donc avant le `set` du meme bloc. Le test comparait une chaine
         vide et laissait tout passer.
      2. refresh-data.bat est en LF, pas en CRLF ; une reecriture melangeait les deux et cassait
         les labels (`goto :verrou_ok` introuvable).
      3. `for /f %%A in ('powershell ... "[int]((Get-Date)-...)"')` DECOUPE la commande sur les
         espaces faute de delims : la boucle rendait `[int]((Get-Date)-(Get-Item` au lieu d'un
         nombre. Donc meme corrige du point 1, le verrou n'aurait jamais compare un age.
    Et surtout : intestable. Le seul essai realiste lancait le vrai .bat, donc un refresh d'1h40
    sur la donnee publique. Ici, tout se teste avec -WhatIf et un dossier bidon.

CONTRAT
    -Acquire  pose le verrou. Sortie 0 = pose, tu peux travailler. Sortie 3 = un run tourne deja,
              n'y va pas (ce n'est PAS une erreur, c'est le cas nominal du planificateur).
    -Release  retire le verrou. Toujours sortie 0 : liberer ne doit jamais faire echouer un run.
    Un verrou de plus de -StaleHours heures est declare ORPHELIN (run precedent tue) et remplace.

    Le journal est un fichier A PART (refresh-verrou.log) et ce n'est pas du rangement :
    refresh.log est tenu OUVERT en append par le .bat pendant tout son run, donc y ecrire depuis
    ici leve "Permission refusee" - exactement dans le cas ou le verrou a quelque chose a dire.
    Attrape au test le 29/07.
#>
[CmdletBinding(DefaultParameterSetName = 'Acquire')]
param(
    [Parameter(ParameterSetName = 'Acquire')][switch]$Acquire,
    [Parameter(ParameterSetName = 'Release')][switch]$Release,
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [int]$StaleHours = 4
)

$ErrorActionPreference = 'Stop'
$lock = Join-Path $Root 'refresh.lock'
$log  = Join-Path $Root 'refresh-verrou.log'

function Write-Journal([string]$msg) {
    # Le journal ne doit JAMAIS faire echouer le verrou : s'il n'est pas ecrivable, on continue.
    try { Add-Content -LiteralPath $log -Value ("==== " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " " + $msg) -Encoding utf8 } catch { }
}

if ($Release) {
    if (Test-Path -LiteralPath $lock) {
        try { Remove-Item -LiteralPath $lock -Force } catch { Write-Journal "liberation IMPOSSIBLE : $($_.Exception.Message)" }
    }
    exit 0
}

if (Test-Path -LiteralPath $lock) {
    $ageMin = [int]((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalMinutes
    if ($ageMin -lt ($StaleHours * 60)) {
        Write-Journal "refresh IGNORE : un run tourne deja (verrou pose il y a $ageMin min)"
        Write-Output "un run tourne deja (verrou de $ageMin min)"
        exit 3
    }
    Write-Journal "verrou ORPHELIN de $ageMin min supprime (run precedent tue), on repart"
    Remove-Item -LiteralPath $lock -Force
}

Set-Content -LiteralPath $lock -Value ("refresh demarre " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding utf8
exit 0
