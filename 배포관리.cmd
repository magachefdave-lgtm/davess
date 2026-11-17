@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :folder_error
if exist "BUILD_INCOMPLETE.txt" goto :incomplete
if exist "Admin\WLR_OnAir_Admin.exe" goto :built
if exist "WLR_OnAir_Admin.exe" goto :adjacent
if exist "build.py" goto :source_folder
echo 관리자용 EXE를 찾을 수 없습니다.
echo 소스 폴더에서는 build_windows.cmd로 먼저 빌드하세요.
echo 빌드 완료 폴더 안의 배포관리.cmd를 실행하세요.
pause
exit /b 1
:source_folder
rem Root-level copy in the SOURCE folder: point to the newest finished build folder.
echo 이 파일은 빌드가 끝난 폴더에서 실행하세요.
echo 지금 폴더는 소스 폴더입니다. build_windows.cmd 로 빌드하면 dist 폴더 안에 빌드 완료 폴더가 생깁니다.
set "ONAIR_LATEST="
for /f "delims=" %%D in ('dir /b /ad /o-n "dist\source-build-*" 2^>nul') do if not defined ONAIR_LATEST if exist "dist\%%D\BUILD_SUCCESS.txt" set "ONAIR_LATEST=%%D"
if not defined ONAIR_LATEST goto :no_build
echo 가장 최근 빌드 완료 폴더를 탐색기로 엽니다: dist\%ONAIR_LATEST%
echo 열린 폴더 안의 배포관리.cmd 를 더블클릭하세요.
start "" explorer.exe "%~dp0dist\%ONAIR_LATEST%"
pause
exit /b 1
:no_build
echo 아직 빌드가 끝난 폴더가 없습니다. 먼저 build_windows.cmd 를 실행하세요.
pause
exit /b 1
:incomplete
echo 빌드가 완료되지 않은 폴더입니다. 빌드 오류를 해결한 뒤 실행하세요.
pause
exit /b 1
:built
start "" "%~dp0Admin\WLR_OnAir_Admin.exe" --publish
exit /b
:adjacent
start "" "%~dp0WLR_OnAir_Admin.exe" --publish
exit /b
:folder_error
echo [오류] 이 파일이 있는 폴더로 이동하지 못했습니다.
echo 네트워크 공유 폴더에서 실행했다면 빌드 완료 폴더를 내 PC의 로컬 폴더로 복사한 뒤 다시 실행하세요.
pause
exit /b 1
