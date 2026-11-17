@echo off
setlocal DisableDelayedExpansion
rem Keep this file ASCII-only. py -0p writes UTF-8 and FOR /F decodes it with the
rem active code page, so UTF-8 keeps non-ASCII user-profile Python paths intact.
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 goto :folder_error
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "ONAIR_FOUND="
set "ONAIR_CANDIDATES=0"
set "BUILD_EXIT=1"
if not exist "build_logs" mkdir "build_logs" >nul 2>&1
:new_log
set "ONAIR_LOG=build_logs\python_preflight_%RANDOM%_%RANDOM%.log"
if exist "%ONAIR_LOG%" goto :new_log
> "%ONAIR_LOG%" echo ON AIR Python preflight - path-free runtime summary
if not errorlevel 1 goto :log_ready
set "ONAIR_LOG="
echo WARNING: Cannot save a preflight log. Keep this window or a screenshot.
:log_ready
set "ONAIR_MESSAGE=Checking installed Python. Required: CPython 3.11-3.14 final, Windows AMD64, normal GIL."
call :report
set "ONAIR_SOURCE=launcher"
rem Listing is read-only even if an administrator enabled install-on-demand.
rem Never execute the launcher with a Python version request.
for /f "tokens=1,*" %%A in ('py -0p 2^>nul') do (
  set "ONAIR_LAUNCHER_TAG=%%A"
  set "ONAIR_PYTHON_CANDIDATE=%%B"
  if not defined ONAIR_FOUND call :from_launcher
)
if defined ONAIR_FOUND goto :result
set "ONAIR_MESSAGE=Checking PATH for existing python.exe files. Store aliases are skipped."
call :report
set "ONAIR_SOURCE=PATH"
rem Resolve an existing PATH executable; never invoke a Store Python alias.
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
  set "ONAIR_PYTHON_CANDIDATE=%%P"
  if not defined ONAIR_FOUND call :try_candidate
)
if defined ONAIR_FOUND goto :result
if not "%ONAIR_CANDIDATES%"=="0" goto :python_failed
set "ONAIR_MESSAGE=No installed Python candidates were returned by py -0p or PATH."
call :report
set "ONAIR_MESSAGE=Python may be absent, or its Launcher/PATH registration may be missing."
call :report
:python_failed
set "ONAIR_MESSAGE=PYTHON CHECK FAILED. The EXE build has not started."
call :report
echo Review the candidate reasons above. Do not just rerun the same file.
echo If no supported Python is installed, install a normal 64-bit CPython 3.11-3.14.
echo https://www.python.org/downloads/windows/
echo This build file never installs Python or opens the Microsoft Store.
echo Korean step-by-step guide: the .txt file whose name means START in this folder, step 1.
echo To check registrations, open Command Prompt and run: py -0p
echo See WINDOWS_BUILD_GUIDE.md, section 5, for the matching reason.
if defined ONAIR_LOG echo Share this diagnostic log if needed: "%ONAIR_LOG%"
echo This preflight log does not include personal paths or environment variables.
echo.
pause
exit /b 1

:from_launcher
rem Ignore headings and the launcher's "No installed Pythons" message.
if "%ONAIR_LAUNCHER_TAG:~0,1%"=="-" goto :try_candidate
if "%ONAIR_LAUNCHER_TAG%"=="*" goto :try_candidate
if "%ONAIR_LAUNCHER_TAG%"=="(venv)" goto :try_candidate
exit /b 0

:normalize_candidate
rem Quotes cannot be part of a Windows filename. Preserve spaces in the path.
set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:"=%"
for /f "tokens=*" %%P in ("%ONAIR_PYTHON_CANDIDATE%") do set "ONAIR_PYTHON_CANDIDATE=%%P"
if "%ONAIR_PYTHON_CANDIDATE:~0,1%"=="*" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~1%"
for /f "tokens=*" %%P in ("%ONAIR_PYTHON_CANDIDATE%") do set "ONAIR_PYTHON_CANDIDATE=%%P"
if "%ONAIR_PYTHON_CANDIDATE:~-2%"==" *" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~0,-2%"
exit /b 0

:try_candidate
rem The path is held in an environment variable, never interpolated into CALL.
call :normalize_candidate
if not defined ONAIR_PYTHON_CANDIDATE exit /b 0
set /a ONAIR_CANDIDATES+=1 >nul
set "ONAIR_MESSAGE=Candidate %ONAIR_CANDIDATES% from %ONAIR_SOURCE%: checking installed executable."
call :report
if /i not "%ONAIR_PYTHON_CANDIDATE:WindowsApps=%"=="%ONAIR_PYTHON_CANDIDATE%" goto :store_alias
if not exist "%ONAIR_PYTHON_CANDIDATE%" goto :missing_candidate
"%ONAIR_PYTHON_CANDIDATE%" -I "%~dp0build_preflight.py" "%ONAIR_LOG%" 2>nul
set "ONAIR_PROBE_EXIT=%ERRORLEVEL%"
if "%ONAIR_PROBE_EXIT%"=="10" exit /b 0
if not "%ONAIR_PROBE_EXIT%"=="0" goto :probe_failed
set "ONAIR_FOUND=1"
echo Using installed Python: "%ONAIR_PYTHON_CANDIDATE%"
"%ONAIR_PYTHON_CANDIDATE%" -I "%~dp0build_windows.py"
set "BUILD_EXIT=%ERRORLEVEL%"
exit /b 0

:store_alias
set "ONAIR_MESSAGE=SKIPPED: WindowsApps alias skipped; it may open the Store or a manager."
call :report
exit /b 0

:missing_candidate
set "ONAIR_MESSAGE=REJECTED: the listed executable path does not exist or is not accessible."
call :report
exit /b 0

:probe_failed
set "ONAIR_MESSAGE=PROBE COULD NOT RUN, exit %ONAIR_PROBE_EXIT%. Python may be damaged, too old for isolated mode, or blocked."
call :report
set "ONAIR_MESSAGE=Check a normal CPython installation and security notices; do not disable protection."
call :report
exit /b 0

:report
rem Messages contain controlled status text only, never paths or raw tool output.
echo %ONAIR_MESSAGE%
if defined ONAIR_LOG >> "%ONAIR_LOG%" echo %ONAIR_MESSAGE%
exit /b 0

:result
if not "%BUILD_EXIT%"=="0" echo BUILD FAILED. See build_logs and WINDOWS_BUILD_GUIDE.md.
echo.
pause
exit /b %BUILD_EXIT%

:folder_error
echo Extract the entire ZIP to a writable local folder before building. Do not build on a network share.
pause
exit /b 1
