# Crea los accesos directos "Contabilidad Angel Lecompte" en el escritorio y en el menú Inicio.
# El acceso directo abre el programa sin ventana negra (pythonw.exe) y con su propio icono; si el programa ya
# está abierto, solo muestra el navegador. Lo ejecuta iniciar.bat / Contabilidad.bat la primera vez.
$carpeta = $PSScriptRoot
$nombre = "Contabilidad Angel Lecompte"
$icono = Join-Path $carpeta "app\static\icono.ico"
$pythonw = Join-Path $carpeta "python\pythonw.exe"              # paquete portable
if (-not (Test-Path $pythonw)) { $pythonw = Join-Path $carpeta ".venv\Scripts\pythonw.exe" }  # instalación con Python
if (Test-Path $pythonw) {
  $destino = $pythonw
  $argumentos = "-m app"
  $ventana = 1
} else {
  $destino = Join-Path $carpeta "Contabilidad.bat"
  if (-not (Test-Path $destino)) { $destino = Join-Path $carpeta "iniciar.bat" }
  $argumentos = ""
  $ventana = 7
}
$shell = New-Object -ComObject WScript.Shell
$lugares = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
foreach ($lugar in $lugares) {
  if (-not (Test-Path $lugar)) { continue }
  $acceso = $shell.CreateShortcut((Join-Path $lugar "$nombre.lnk"))
  $acceso.TargetPath = $destino
  $acceso.Arguments = $argumentos
  $acceso.WorkingDirectory = $carpeta
  $acceso.WindowStyle = $ventana
  $acceso.Description = "Contabilidad Angel Lecompte S.A.S."
  if (Test-Path $icono) { $acceso.IconLocation = "$icono,0" }
  $acceso.Save()
}
Write-Host "Accesos directos creados en el escritorio y en el menú Inicio."
