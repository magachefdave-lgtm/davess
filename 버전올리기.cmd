@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :folder_error
rem Double-click helper: asks for a new version and updates VERSION, backend/main.py,
rem entrypoint.py, SOURCE_MANIFEST.json and SHA256SUMS.txt through build.py --bump-version.
rem File rule: CP949 + CRLF, no chcp. Korean text appears only on echo lines.
rem The Python search below is the same as collect_wheels.cmd (installed Python only).
if not exist "build.py" goto :not_source
if not exist "VERSION" goto :not_source
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "ONAIR_PY="
set "ONAIR_CANDIDATES=0"
set "ONAIR_STORE_ALIAS="
echo 버전 올리기 - 설치된 Python을 찾는 중입니다...
for /f "tokens=1,*" %%A in ('py -0p 2^>nul') do (
  set "ONAIR_LAUNCHER_TAG=%%A"
  set "ONAIR_PYTHON_CANDIDATE=%%B"
  if not defined ONAIR_PY call :from_launcher
)
if defined ONAIR_PY goto :have_python
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
  set "ONAIR_PYTHON_CANDIDATE=%%P"
  if not defined ONAIR_PY call :try_candidate
)
if defined ONAIR_PY goto :have_python
for %%V in (314 313 312 311) do (
  set "ONAIR_PYTHON_CANDIDATE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if not defined ONAIR_PY call :try_candidate
  set "ONAIR_PYTHON_CANDIDATE=%ProgramFiles%\Python%%V\python.exe"
  if not defined ONAIR_PY call :try_candidate
)
for %%V in (3.14 3.13 3.12 3.11) do (
  set "ONAIR_PYTHON_CANDIDATE=%LOCALAPPDATA%\Python\pythoncore-%%V-64\python.exe"
  if not defined ONAIR_PY call :try_candidate
)
if defined ONAIR_PY goto :have_python
goto :no_python

:have_python
echo.
"%ONAIR_PY%" -I -B "%~dp0build.py" --bump-version
if errorlevel 1 goto :failed
echo.
pause
exit /b 0

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
if /i not "%ONAIR_PYTHON_CANDIDATE:WindowsApps=%"=="%ONAIR_PYTHON_CANDIDATE%" goto :store_alias
if not exist "%ONAIR_PYTHON_CANDIDATE%" exit /b 0
set /a ONAIR_CANDIDATES+=1 >nul
"%ONAIR_PYTHON_CANDIDATE%" -I -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 0
set "ONAIR_PY=%ONAIR_PYTHON_CANDIDATE%"
exit /b 0

:store_alias
set "ONAIR_STORE_ALIAS=1"
exit /b 0

:failed
echo.
echo 버전을 바꾸지 못했습니다. 위 [오류] 문장을 확인하세요. 실패하면 파일은 원래대로 남습니다.
pause
exit /b 1

:no_python
echo.
echo [오류] Python을 찾지 못했습니다. 아무 파일도 바꾸지 않았습니다.
echo 시작하기.txt 의 1단계대로 Python 3.11~3.14 64비트를 설치한 뒤 다시 실행하세요.
pause
exit /b 1

:not_source
echo [오류] 이 파일은 소스 폴더 build.py 와 VERSION 파일이 있는 폴더에서 실행하세요.
echo 빌드가 끝난 폴더 dist 안에서는 버전을 바꿀 수 없습니다.
pause
exit /b 1

:folder_error
echo [오류] 이 파일이 있는 폴더로 이동하지 못했습니다.
echo 네트워크 공유 폴더라면 내 PC의 로컬 폴더로 복사한 뒤 다시 실행하세요.
pause
exit /b 1
