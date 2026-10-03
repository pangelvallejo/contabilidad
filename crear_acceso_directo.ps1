# Crea un acceso directo en el escritorio que abre el programa.
$destino = Join-Path $PSScriptRoot "iniciar.bat"
$escritorio = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$acceso = $shell.CreateShortcut((Join-Path $escritorio "Contabilidad Angel Lecompte.lnk"))
$acceso.TargetPath = $destino
$acceso.WorkingDirectory = $PSScriptRoot
$acceso.WindowStyle = 7
$acceso.Save()
Write-Host "Acceso directo creado en el escritorio."
