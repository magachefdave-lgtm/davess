@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :folder_error
rem Run this on a PC WITH internet access. It fills wheels\ for offline builds.
rem File rule: CP949 + CRLF, no chcp. Korean text appears only on echo lines.
rem py -0p writes UTF-8, so a non-ASCII launcher path can arrive garbled here.
rem Such a candidate fails the existence check; PATH and the default install folders follow.
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "ONAIR_PY="
set "ONAIR_CANDIDATES=0"
set "ONAIR_STORE_ALIAS="
set "ONAIR_EXIT="
echo 설치된 Python을 찾는 중입니다...
rem Listing is read-only. Never execute the launcher with a Python version request.
for /f "tokens=1,*" %%A in ('py -0p 2^>nul') do (
  set "ONAIR_LAUNCHER_TAG=%%A"
  set "ONAIR_PYTHON_CANDIDATE=%%B"
  if not defined ONAIR_PY call :from_launcher
)
if defined ONAIR_PY goto :have_python
rem Resolve an existing PATH executable; never invoke a Store Python alias.
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
  set "ONAIR_PYTHON_CANDIDATE=%%P"
  if not defined ONAIR_PY call :try_candidate
)
if defined ONAIR_PY goto :have_python
rem Default python.org and Python install manager folders. These paths come from
rem environment variables, not from tool output, so the console code page cannot garble them.
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
echo 사용할 Python: "%ONAIR_PY%"
"%ONAIR_PY%" -I "%~dp0collect_wheels.py"
set "ONAIR_EXIT=%ERRORLEVEL%"
if "%ONAIR_EXIT%"=="0" goto :done
if "%ONAIR_EXIT%"=="13" goto :download_failed
if "%ONAIR_EXIT%"=="12" goto :no_pip
if "%ONAIR_EXIT%"=="11" goto :source_incomplete
goto :failed

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

:done
echo.
echo 완료: wheels 폴더가 준비되었습니다.
echo wheels 폴더를 포함한 소스 폴더 전체를 사내망 PC로 옮긴 뒤 build_windows.cmd를 실행하세요.
pause
exit /b 0

:no_python
echo.
echo [오류] Python을 찾지 못했습니다. 아직 아무것도 내려받지 않았습니다.
if "%ONAIR_CANDIDATES%"=="0" goto :no_python_store
echo 설치된 python.exe %ONAIR_CANDIDATES%개를 찾았지만 실행되지 않았습니다. 손상되었거나 보안 프로그램이 막았을 수 있습니다.
:no_python_store
if not defined ONAIR_STORE_ALIAS goto :no_python_hint
echo Microsoft Store 바로가기 WindowsApps python.exe는 Store 창을 열 수 있어 건너뛰었습니다.
:no_python_hint
echo python.org에서 Windows 64-bit용 CPython 3.11~3.14를 설치한 뒤 이 파일을 다시 실행하세요.
echo https://www.python.org/downloads/windows/
echo 설치 화면의 py launcher 항목을 켜 두면 자동으로 찾습니다.
pause
exit /b 1

:download_failed
echo.
echo [오류] 다운로드 실패 - 인터넷 연결 또는 회사 네트워크의 PyPI 차단 여부를 확인하세요.
echo Python은 정상적으로 찾았습니다: "%ONAIR_PY%"
echo pip 메시지 전체가 build_logs 폴더의 collect_wheels_날짜.log 파일에 저장되었습니다. 위에 정확한 파일 경로가 있습니다.
echo IT 담당자에게 그 로그 파일을 보내 pypi.org, files.pythonhosted.org 접속 허용 여부를 문의하세요.
echo 로그에 HASHES 불일치가 보이면 네트워크 문제가 아니니 그 로그 파일을 그대로 전달하세요.
echo 인증서 검증을 끄거나 --trusted-host 옵션을 붙이지 마세요.
pause
exit /b 1

:no_pip
echo.
echo [오류] 찾은 Python에 pip가 없어 내려받을 수 없습니다: "%ONAIR_PY%"
echo python.org 설치 프로그램의 수정 Modify 메뉴에서 pip 항목을 켜거나 다시 설치하세요.
pause
exit /b 1

:source_incomplete
echo.
echo [오류] 소스 폴더에 빌드 락 파일 requirements-build-py31x.txt 가 없습니다.
echo ZIP 전체를 새 폴더에 다시 압축 해제한 뒤 실행하세요.
pause
exit /b 1

:failed
echo.
echo [오류] wheel 수집 중 예상하지 못한 오류가 났습니다. 종료 코드 %ONAIR_EXIT%
echo 위 메시지를 캡처해 전달하세요.
pause
exit /b 1

:folder_error
echo [오류] 이 파일이 있는 폴더로 이동하지 못했습니다.
echo 네트워크 공유 폴더라면 내 PC의 로컬 폴더로 복사한 뒤 다시 실행하세요.
pause
exit /b 1
