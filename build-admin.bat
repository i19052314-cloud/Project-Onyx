@echo off
setlocal EnableDelayedExpansion
title ONYX ADMIN - build and install

rem ===========================================================================
rem  ONYX ADMIN - companion admin panel for Onyx RP.
rem
rem  Builds src\OnyxAdmin\OnyxAdmin.csproj and installs it into a GTA V folder.
rem
rem  Visual Studio is NOT required. The script uses MSBuild when it is there and
rem  otherwise falls back to csc.exe from the .NET Framework directory - the
rem  compiler that is already on any machine able to run GTA V, since
rem  ScriptHookVDotNet needs .NET Framework 4.8 anyway.
rem
rem  Usage:
rem    build-admin.bat                       find the game folder automatically
rem    build-admin.bat "D:\Games\GTA V"      or point at it
rem
rem  This installs a companion module. It does not install Onyx RP itself and it
rem  never touches ProjectOnyx's files.
rem ===========================================================================

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

set "PROJNAME=OnyxAdmin"
set "SRC=%REPO%\src\%PROJNAME%"
set "CSPROJ=%SRC%\%PROJNAME%.csproj"
set "OUT=%SRC%\bin\Release\%PROJNAME%.dll"
set "LIB=%REPO%\lib\shvdn3"
set "INI=%REPO%\%PROJNAME%\%PROJNAME%.ini"

echo.
echo  ==========================================================
echo    ONYX ADMIN  -  build and install
echo  ==========================================================
echo.
echo    Source folder:
echo      %REPO%
echo.

if not exist "%CSPROJ%" (
  echo    ERROR: %CSPROJ%
  echo           not found. Run this from the repository root.
  goto :fail
)

rem ---------------------------------------------------------------------------
rem  1. locate the game folder
rem ---------------------------------------------------------------------------
set "GTA=%~1"
if defined GTA goto :have_arg

echo    Looking for GTA V...

for %%P in (
  "C:\Program Files\Steam\steamapps\common\Grand Theft Auto V"
  "C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V"
  "D:\Steam\steamapps\common\Grand Theft Auto V"
  "E:\Steam\steamapps\common\Grand Theft Auto V"
  "C:\Program Files\Rockstar Games\Grand Theft Auto V"
  "C:\Program Files (x86)\Rockstar Games\Grand Theft Auto V"
  "D:\Rockstar Games\Grand Theft Auto V"
  "E:\Rockstar Games\Grand Theft Auto V"
  "D:\Games\Grand Theft Auto V"
  "E:\Games\Grand Theft Auto V"
) do (
  if not defined GTA (
    if exist "%%~P\GTA5.exe" set "GTA=%%~P"
    if not defined GTA (
      if exist "%%~P\GTA5_Enhanced.exe" set "GTA=%%~P"
    )
  )
)

if not defined GTA (
  echo.
  echo    GTA V was not found automatically.
  echo.
  echo    Run this file again with the game folder as an argument:
  echo      build-admin.bat "D:\Games\Grand Theft Auto V"
  goto :fail
)

:have_arg
if not exist "%GTA%\GTA5.exe" (
  if not exist "%GTA%\GTA5_Enhanced.exe" (
    echo    ERROR: "%GTA%" is not a GTA V folder - no GTA5.exe inside.
    goto :fail
  )
)
echo    Game folder:  %GTA%

rem ---------------------------------------------------------------------------
rem  2. reference assembly
rem ---------------------------------------------------------------------------
if exist "%LIB%\ScriptHookVDotNet3.dll" goto :have_ref

echo    ScriptHookVDotNet3.dll is not in lib\shvdn3 yet.

if not exist "%GTA%\ScriptHookVDotNet3.dll" (
  echo.
  echo    ERROR: ScriptHookVDotNet3.dll was not found in the game folder either.
  echo.
  echo    Install ScriptHookVDotNet 3.6 or 3.7 first, then run this again.
  goto :fail
)

if not exist "%LIB%" mkdir "%LIB%"
copy /y "%GTA%\ScriptHookVDotNet3.dll" "%LIB%" >nul
if errorlevel 1 (
  echo    ERROR: could not copy ScriptHookVDotNet3.dll into lib\shvdn3.
  goto :fail
)
echo    Copied ScriptHookVDotNet3.dll into lib\shvdn3.

:have_ref
echo    Reference:   %LIB%\ScriptHookVDotNet3.dll

rem ---------------------------------------------------------------------------
rem  3. compiler
rem ---------------------------------------------------------------------------
set "CSC="
set "MSBUILD="

for %%M in (
  "%ProgramFiles%\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin\MSBuild.exe"
  "%ProgramFiles(x86)%\Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
) do (
  if not defined MSBUILD (
    if exist "%%~M" set "MSBUILD=%%~M"
  )
)

if defined MSBUILD goto :have_msbuild

if exist "%WinDir%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" (
  set "CSC=%WinDir%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
  goto :have_csc
)
if exist "%WinDir%\Microsoft.NET\Framework\v4.0.30319\csc.exe" (
  set "CSC=%WinDir%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
  goto :have_csc
)

echo.
echo    ERROR: no C# compiler found.
echo.
echo    Looked for MSBuild and for csc.exe under:
echo      %WinDir%\Microsoft.NET\Framework64\v4.0.30319\
echo.
echo    csc.exe ships with the .NET Framework, which GTA V and
echo    ScriptHookVDotNet already require - so if it is missing, the .NET
echo    Framework installation itself is broken. Reinstall .NET Framework 4.8
echo    and run this file again.
goto :fail

:have_msbuild
echo    Building with MSBuild...
"%MSBUILD%" "%CSPROJ%" /p:Configuration=Release /v:minimal /nologo
if errorlevel 1 (
  echo.
  echo    ERROR: MSBuild reported a failure. The compiler output above says why.
  goto :fail
)
goto :built

:have_csc
echo    Building with csc.exe (no Visual Studio needed)...
for %%C in ("%CSC%") do set "FWDIR=%%~dpC"
set "FWDIR=%FWDIR:~0,-1%"

set "RSP=%SRC%\build-admin.rsp"
if not exist "%SRC%\bin\Release" mkdir "%SRC%\bin\Release"
if exist "%OUT%" del /f /q "%OUT%"
if exist "%RSP%" del /f /q "%RSP%"

> "%RSP%" echo /nologo
>>"%RSP%" echo /target:library
>>"%RSP%" echo /optimize+
>>"%RSP%" echo /define:TRACE
>>"%RSP%" echo /out:"%OUT%"
>>"%RSP%" echo /reference:"%LIB%\ScriptHookVDotNet3.dll"
for %%R in (System.dll System.Core.dll System.Drawing.dll System.Windows.Forms.dll System.Runtime.Serialization.dll System.Xml.dll System.Xml.Linq.dll) do >>"%RSP%" echo /reference:"%FWDIR%\%%R"
for /f "usebackq tokens=*" %%F in (`dir /s /b "%SRC%\*.cs" 2^>nul ^| findstr /v /i "\\obj\\" ^| findstr /v /i "\\bin\\"`) do >>"%RSP%" echo "%%F"

"%CSC%" /langversion:7.3 @"%RSP%"
if errorlevel 1 (
  echo    The compiler refused /langversion:7.3 - retrying without it.
  "%CSC%" @"%RSP%"
  if errorlevel 1 (
    echo.
    echo    ERROR: compilation failed. The compiler output above says why.
    goto :fail
  )
)

:built
if not exist "%OUT%" (
  echo.
  echo    ERROR: the build produced no DLL.
  goto :fail
)
echo    [ok] built: %OUT%

rem ---------------------------------------------------------------------------
rem  4. install
rem ---------------------------------------------------------------------------
echo.
echo    Installing...

if not exist "%GTA%\scripts" mkdir "%GTA%\scripts"

copy /y "%OUT%" "%GTA%\scripts" >nul
if errorlevel 1 (
  echo    ERROR: could not copy the DLL. Is the game running?
  goto :fail
)
echo      scripts\%PROJNAME%.dll

if exist "%INI%" (
  copy /y "%INI%" "%GTA%\scripts" >nul
  if errorlevel 1 (
    echo    WARNING: could not copy %PROJNAME%.ini - the defaults will be used.
  ) else (
    echo      scripts\%PROJNAME%.ini
  )
) else (
  echo    WARNING: %PROJNAME%.ini not found in the repository - defaults will be used.
)

if not exist "%GTA%\%PROJNAME%" mkdir "%GTA%\%PROJNAME%"

rem ---------------------------------------------------------------------------
rem  5. verify
rem ---------------------------------------------------------------------------
set "BAD=0"
if not exist "%GTA%\scripts\%PROJNAME%.dll" set "BAD=1"

if "%BAD%"=="1" (
  echo.
  echo    ERROR: verification failed - the DLL is not in scripts\.
  goto :fail
)

echo.
echo  ==========================================================
echo    INSTALLED.
echo.
echo    In game:
echo      F5                 open or close the admin panel
echo      Up / Down          move between rows
echo      Left / Right       choose weapon or vehicle
echo      Enter              apply the row
echo      Backspace          close the panel
echo.
echo    Settings: %GTA%\scripts\%PROJNAME%.ini
echo    Log:      %GTA%\%PROJNAME%\onyx-admin.log
echo.
echo    If Onyx RP is running, press F9 first to enable it.
echo  ==========================================================
echo.
endlocal
exit /b 0

:fail
echo.
echo    Nothing was changed that cannot be repeated. Fix the problem above
echo    and run this file again.
echo.
endlocal
pause
exit /b 1
