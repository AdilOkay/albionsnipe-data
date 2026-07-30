' ============================================================================
' City Buy List - lanceur silencieux du refresh de donnees.
'
' POURQUOI CE FICHIER EXISTE :
' Le planificateur de taches Windows lancait refresh-data.bat directement, ce qui
' ouvrait une fenetre de console VISIBLE en pleine journee. Comme refresh-data.bat
' redirige toute sa sortie vers refresh.log, cette fenetre restait NOIRE ET VIDE
' pendant ~1h39. Resultat previsible : elle se faisait fermer a la main (reaction
' saine face a une fenetre noire inexpliquee), ce qui tuait le refresh en cours.
' Constate les 14/07 et 15/07/2026 : code de sortie 0xC000013A (CTRL+C), donnees
' figees a J-1.
'
' CE QUE CA FAIT :
' Lance refresh-data.bat dans le MEME dossier que ce script, en fenetre CACHEE
' (parametre 0), attend la fin (True), et renvoie son code de sortie au
' planificateur pour que LastTaskResult reste fiable (0 = succes).
'
' USAGE :
' Reserve au planificateur de taches. Pour un refresh manuel, double-cliquer
' refresh-data.bat : la fenetre visible est normale et voulue dans ce cas.
' ============================================================================
Option Explicit
Dim sh, fso, here, bat, rc

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' meme dossier que ce script (equivalent VBS du %~dp0 de refresh-data.bat)
here = fso.GetParentFolderName(WScript.ScriptFullName)
bat = fso.BuildPath(here, "refresh-data.bat")

If Not fso.FileExists(bat) Then
  ' code distinctif : le planificateur montrera 2 au lieu d'un echec muet
  WScript.Quit 2
End If

' ---------------------------------------------------------------------------
' VERROU D'EXCLUSION MUTUELLE (ajoute le 29/07/2026)
'
' POURQUOI : le 29/07, TROIS build_routes.py ont tourne en meme temps contre AODP
' (le refresh planifie, plus deux lancements manuels). Consequences en chaine, toutes
' silencieuses : l'API a repondu HTTP 429 et un lot de 50 items est parti sans prix,
' et le run le plus lent a fini APRES la publication en ecrasant sur le disque une
' version meilleure de 90 items. Aucun des trois n'a signale quoi que ce soit : chacun
' se croyait seul et a rapporte un succes.
'
' Le verrou est un simple fichier. Il porte l'heure de son depot, ce qui permet de
' distinguer un run VIVANT d'un verrou ORPHELIN laisse par un run tue (coupure, arret
' machine). Au-dela de PERIME_HEURES, on considere le verrou mort et on repart : un
' verrou qui ne se libere jamais bloque la donnee pour toujours, ce qui est pire que
' le probleme qu'il resout.
'
' Codes de sortie distincts, pour que le planificateur dise QUOI :
'   0  succes           2  refresh-data.bat introuvable
'   3  un refresh tourne deja, on ne fait rien (cas nominal, pas une erreur)
' ---------------------------------------------------------------------------
Const PERIME_HEURES = 4

' Le journal du verrou est un fichier A PART, et ce n'est pas un detail de rangement :
' refresh.log est tenu OUVERT en append par le .bat pendant tout son run (~1h40), et une
' tentative d'ecriture dessus depuis le VBS leve "Permission refusee". Or c'est
' exactement dans ce cas-la, un run deja en cours, que le verrou a quelque chose a dire.
' Ecrire le refus dans le fichier tenu par celui qu'on refuse etait donc garanti d'echouer
' au seul moment qui compte. Attrape au test, le 29/07.
Dim verrou, logf, f, ageH
verrou = fso.BuildPath(here, "refresh.lock")
logf = fso.BuildPath(here, "refresh-verrou.log")

If fso.FileExists(verrou) Then
  ageH = (Now - fso.GetFile(verrou).DateLastModified) * 24
  If ageH < PERIME_HEURES Then
    On Error Resume Next
    Set f = fso.OpenTextFile(logf, 8, True)
    f.WriteLine "==== " & Now & " refresh IGNORE : un run tourne deja (verrou pose il y a " _
                & FormatNumber(ageH * 60, 0) & " min) ===="
    f.Close
    On Error GoTo 0
    WScript.Quit 3
  Else
    On Error Resume Next
    Set f = fso.OpenTextFile(logf, 8, True)
    f.WriteLine "==== " & Now & " verrou ORPHELIN de " & FormatNumber(ageH, 1) _
                & "h supprime (run precedent tue), on repart ===="
    f.Close
    On Error GoTo 0
    fso.DeleteFile verrou, True
  End If
End If

Set f = fso.CreateTextFile(verrou, True)
f.WriteLine "refresh demarre " & Now
f.Close

On Error Resume Next
rc = sh.Run("""" & bat & """ /locked", 0, True)   ' /locked : le verrou est deja pose ici (30/07)
Dim err_run
err_run = Err.Number
On Error GoTo 0

' Le verrou se libere QUOI QU'IL ARRIVE, y compris si le .bat plante : sinon le
' premier echec gele la donnee pendant PERIME_HEURES.
If fso.FileExists(verrou) Then fso.DeleteFile verrou, True

If err_run <> 0 Then WScript.Quit 4
WScript.Quit rc
