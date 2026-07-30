@echo off
REM ============================================================================
REM City Buy List - local price-data refresh.
REM
REM Rebuilds baseline / materials / toptraded / routes from AODP and pushes them
REM to this public repo, so the app's launcher pulls fresh averages on start.
REM
REM Runs HERE (your machine) on purpose: AODP throttles GitHub Actions' datacenter
REM IPs, so the CI refresh times out. Your home IP is not throttled - a full run
REM takes ~20-30 min and completes cleanly.
REM
REM Double-click to refresh now, or register it with Windows Task Scheduler (see
REM the SETUP note the assistant gave you). Output is appended to refresh.log.
REM ============================================================================
setlocal
cd /d "%~dp0"
set "LOG=%~dp0refresh.log"
set "OURLOCK="

REM VERROU (30/07). Toute la logique vit dans tools\refresh-lock.ps1, qui se teste seul :
REM sortie 3 = un run tourne deja, sortie 0 = verrou pose. Rien n'est calcule en batch, parce
REM qu'une premiere version qui le faisait avait trois bugs invisibles a la relecture (bloc
REM parenthese, fins de ligne, for /f qui decoupait la commande PowerShell).
REM Quand c'est refresh-hidden.vbs qui nous lance, il a DEJA pose le verrou et le retire lui-meme :
REM il passe /locked et on n'y touche pas. Sans ce drapeau, ce test se refuserait le verrou du VBS
REM et le refresh planifie ne tournerait plus jamais.
if /i "%~1"=="/locked" goto :verrou_ok
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\refresh-lock.ps1" -Acquire
REM `if errorlevel 3 endlocal & exit /b 3` ne marche PAS : le `&` n'est pas dans la portee du if,
REM donc le exit partirait a tous les coups. Attrape au test le 30/07. Un goto, et c'est sans piege.
if errorlevel 3 goto :verrou_pris
set "OURLOCK=1"
goto :verrou_ok

:verrou_pris
endlocal
exit /b 3

:verrou_ok
echo ==== %DATE% %TIME% refresh start ==== >> "%LOG%"

call python scripts\build_baseline.py   >> "%LOG%" 2>&1
call python scripts\build_materials.py  >> "%LOG%" 2>&1
call python scripts\build_toptraded.py  >> "%LOG%" 2>&1
call python scripts\build_routes.py     >> "%LOG%" 2>&1

git add docs/data/baseline.json docs/data/materials.json docs/data/toptraded.json docs/data/routes.json
git diff --cached --quiet && ( echo no data changes, nothing to commit >> "%LOG%" & goto :done )
git commit -m "chore: local scheduled price data refresh" >> "%LOG%" 2>&1
git push >> "%LOG%" 2>&1 && ( echo pushed OK >> "%LOG%" ) || ( echo PUSH FAILED - check git credentials >> "%LOG%" )

:done
echo ==== %DATE% %TIME% refresh end ==== >> "%LOG%"
REM Libere quoi qu'il arrive, y compris apres un echec : sinon le premier plantage gele la donnee
REM 4h. Si c'est le VBS qui a pose le verrou, c'est lui qui le retire.
if defined OURLOCK powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\refresh-lock.ps1" -Release
endlocal
