@echo off
setlocal
set "CONDA_CMD="
if defined CONDA_BAT if exist "%CONDA_BAT%" set "CONDA_CMD=%CONDA_BAT%"
if not defined CONDA_CMD for /f "delims=" %%C in ('where conda.bat 2^>nul') do if not defined CONDA_CMD set "CONDA_CMD=%%C"
if not defined CONDA_CMD if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_CMD=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_CMD=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%LOCALAPPDATA%\miniconda3\condabin\conda.bat" set "CONDA_CMD=%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%ProgramData%\miniconda3\condabin\conda.bat" set "CONDA_CMD=%ProgramData%\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "%ProgramData%\anaconda3\condabin\conda.bat" set "CONDA_CMD=%ProgramData%\anaconda3\condabin\conda.bat"
if not defined CONDA_CMD if exist "D:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_CMD=D:\ProgramData\miniconda3\condabin\conda.bat"
if not defined CONDA_CMD (
  echo Conda was not found. Set CONDA_BAT to the full path of conda.bat.
  exit /b 1
)
call "%CONDA_CMD%" --version >nul 2>&1
if errorlevel 1 (
  echo Conda was found but could not be started. Check the Conda installation.
  exit /b 1
)
if /i "%~1"=="setup" goto setup
call "%CONDA_CMD%" run -n steam-classroom python --version
if errorlevel 1 (
  echo The steam-classroom environment is missing or cannot run.
  exit /b 1
)
call "%CONDA_CMD%" run --no-capture-output -n steam-classroom python "%~dp0classpilot.py" %*
exit /b %ERRORLEVEL%

:setup
set "CLASSPILOT_CONDA_BAT=%CONDA_CMD%"
call "%CONDA_CMD%" run --no-capture-output -n base python "%~dp0setup_classpilot.py"
exit /b %ERRORLEVEL%
