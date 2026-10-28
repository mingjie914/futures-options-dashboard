$terminalPython = if ($env:OPTIONS_PYTHON) { $env:OPTIONS_PYTHON } else { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
& $terminalPython -X utf8 "$PSScriptRoot\start.py" @args
exit $LASTEXITCODE
