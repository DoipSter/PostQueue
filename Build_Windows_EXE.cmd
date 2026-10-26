@echo off
setlocal
cd /d "%~dp0"
rem PostQueue builder. The build targets the CPU architecture of the Python used (x64 or arm64).
rem   set PY="%LOCALAPPDATA%\Programs\Python\Python312-x64\python.exe"   build with a specific Python
rem   set WIX=<folder containing wix.exe>                                 also build the MSI (WiX Toolset v5)
rem   set SIGN_CERT_THUMBPRINT=<thumbprint>                               sign with a certificate in the Windows store
rem   set SIGN_DLIB=<Azure.CodeSigning.Dlib.dll> and SIGN_METADATA=<metadata.json>   sign with Azure Trusted Signing
rem Outputs in dist\ (NAME = PostQueue-<version>-<arch>):
rem   NAME\  one-folder build (recommended)   NAME.zip  the same, zipped   NAME.exe  portable   NAME.msi  installer
if not defined PY (
  where py >nul 2>nul || (echo Python launcher missing. Set PY to a python.exe path. & pause & exit /b 1)
  set PY=py
)
set PYI_PINNED=6.11.1
%PY% -m PyInstaller --version >nul 2>nul || (echo Build tools missing. Install them with: %PY% -m pip install -r requirements-build.txt & pause & exit /b 1)
for /f "delims=" %%a in ('call %PY% -c "import sysconfig; print({'amd64': 'x64'}.get(sysconfig.get_platform().split('-')[-1], sysconfig.get_platform().split('-')[-1]))"') do set ARCH=%%a
for /f "delims=" %%v in ('call %PY% -c "from version import VERSION; print(VERSION)"') do set VERSION=%%v
for /f "delims=" %%v in ('call %PY% -m PyInstaller --version') do set PYI_FOUND=%%v
set NAME=PostQueue-%VERSION%-%ARCH%
echo PostQueue %VERSION% - building %ARCH%
if not "%PYI_FOUND%"=="%PYI_PINNED%" echo WARNING: PyInstaller %PYI_FOUND% found; builds are only reproducible with %PYI_PINNED% from requirements-build.txt.

%PY% -m pytest --version >nul 2>nul
if errorlevel 1 (
  echo WARNING: pytest not installed - skipping tests. Install requirements-build.txt to enable them.
) else (
  %PY% -m pytest -q -p no:cacheprovider tests
  if errorlevel 1 (echo Tests failed - build aborted. & pause & exit /b 1)
)

if not exist "assets\app.ico" (echo assets\app.ico missing. Run: %PY% make_assets.py & pause & exit /b 1)
rem Intermediate files go under %TEMP% so deep source folders do not exceed the Windows path limit.
rem What goes into the package, and which unused Qt parts are filtered out, is defined in installer\PostQueue.spec.
set PQ_NAME=%NAME%
set PYI_COMMON=--noconfirm --clean --workpath "%TEMP%\%NAME%_build"
rem The one-folder build is made in %TEMP% and mirrored into dist with retries, because cloud-synced
rem folders such as OneDrive briefly lock files that were just written.
set PQ_MODE=onedir
%PY% -m PyInstaller %PYI_COMMON% --distpath "%TEMP%\%NAME%_dist" installer\PostQueue.spec
if errorlevel 1 (echo One-folder build failed. & pause & exit /b 1)
robocopy "%TEMP%\%NAME%_dist\%NAME%" "dist\%NAME%" /MIR /R:10 /W:3 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo Copying the one-folder build into dist failed. & pause & exit /b 1)
set PQ_MODE=onefile
%PY% -m PyInstaller %PYI_COMMON% --distpath "%TEMP%\%NAME%_onefile" installer\PostQueue.spec
if errorlevel 1 (echo Portable build failed. & pause & exit /b 1)
copy /y "%TEMP%\%NAME%_onefile\%NAME%.exe" "dist\%NAME%.exe" >nul

call :sign "dist\%NAME%.exe" "dist\%NAME%\%NAME%.exe" || (echo Signing failed. & pause & exit /b 1)

powershell -NoProfile -Command "Compress-Archive -Path 'dist\%NAME%' -DestinationPath 'dist\%NAME%.zip' -Force"
if errorlevel 1 (echo Zip of the one-folder build failed. & pause & exit /b 1)

rem Each architecture keeps a fixed UpgradeCode so later versions replace earlier ones.
set UPGRADE=7A3F1C2E-9B4D-4E6A-8C15-2D9F6B3A7E02
if "%ARCH%"=="x64" set UPGRADE=7A3F1C2E-9B4D-4E6A-8C15-2D9F6B3A7E01
if defined WIX (
  rem The Install screen comes from the WiX UI extension: "%WIX%\wix.exe" extension add -g WixToolset.UI.wixext/5.0.2
  "%WIX%\wix.exe" build -nologo -arch %ARCH% -ext WixToolset.UI.wixext -d Version=%VERSION% -d UpgradeCode=%UPGRADE% -d "ProjectDir=%CD%\\" -d "SourceDir=%CD%\dist\%NAME%" -d ExeName=%NAME%.exe -out "dist\%NAME%.msi" installer\PostQueue.wxs
  if errorlevel 1 (echo MSI build failed. If the error names WixToolset.UI.wixext, run: "%WIX%\wix.exe" extension add -g WixToolset.UI.wixext/5.0.2 & pause & exit /b 1)
  call :sign "dist\%NAME%.msi" || (echo MSI signing failed. & pause & exit /b 1)
) else (
  echo NOTE: MSI skipped. Set WIX to a folder containing wix.exe ^(WiX Toolset v5^) to build dist\%NAME%.msi.
)

del "dist\%NAME%.wixpdb" 2>nul
copy /y THIRD_PARTY_NOTICES.txt "dist\THIRD_PARTY_NOTICES.txt" >nul
robocopy licenses "dist\licenses" /MIR /R:10 /W:3 /NFL /NDL /NJH /NJS /NP >nul
for %%f in ("dist\%NAME%.exe" "dist\%NAME%.zip" "dist\%NAME%.msi") do if exist %%f certutil -hashfile %%f SHA256 > "%%~f.sha256.txt"
echo Output: dist\%NAME%\ (one-folder), dist\%NAME%.zip, dist\%NAME%.exe (portable)
if exist "dist\%NAME%.msi" echo Output: dist\%NAME%.msi
pause
exit /b 0

:sign
if defined SIGN_CERT_THUMBPRINT (
  signtool sign /sha1 %SIGN_CERT_THUMBPRINT% /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 %*
  exit /b %errorlevel%
)
if defined SIGN_DLIB (
  signtool sign /v /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "%SIGN_DLIB%" /dmdf "%SIGN_METADATA%" %*
  exit /b %errorlevel%
)
echo NOTE: Not signed: %* - SmartScreen and antivirus may warn. See BUILD_AND_TEST_GUIDE.md, section Signing.
exit /b 0
