# Crea un acceso directo en el escritorio que abre el programa.
# Usa Contabilidad.bat (paquete con Python incluido) si existe; si no, iniciar.bat (requiere Python instalado).
$destino = Join-Path $PSScriptRoot "Contabilidad.bat"
if (-not (Test-Path $destino)) { $destino = Join-Path $PSScriptRoot "iniciar.bat" }
$escritorio = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$acceso = $shell.CreateShortcut((Join-Path $escritorio "Contabilidad Angel Lecompte.lnk"))
$acceso.TargetPath = $destino
$acceso.WorkingDirectory = $PSScriptRoot
$acceso.WindowStyle = 7
$acceso.Save()
Write-Host "Acceso directo creado en el escritorio."
