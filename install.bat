@echo off
rem ===========================================================================
rem  ONYX RP: NATIONS ^& WAR - automatic installer
rem
rem  What it does, in order:
rem    1. finds the GTA V folder - automatically, or you type it, or you drag
rem       the game folder onto this file
rem    2. copies ScriptHookVDotNet3.dll from the game folder into lib\shvdn3\
rem       so the project can be compiled
rem    3. builds src\OnyxNations\OnyxNations.csproj in Release
rem         - MSBuild (Visual Studio / Build Tools) if it is installed
rem         - otherwise csc.exe, the C# compiler that ships with the .NET
rem           Framework in C:\Windows - no Visual Studio required
rem         - otherwise an already built DLL, if one is lying there
rem    4. backs up every file it is about to overwrite
rem    5. installs OnyxNations.dll + OnyxNations.ini into <GTA>\scripts\
rem       and Data\*.xml into <GTA>\OnyxNations\Data\
rem    6. verifies that every file landed where the code expects it
rem
rem  Usage:
rem    install.bat                       auto-detect the game folder
rem    install.bat "D:\Games\GTA V"      use this folder
rem    install.bat  (or)  drag the GTA V folder onto install.bat
rem
rem  Nothing is deleted. Saves in <GTA>\OnyxNations\Saves are never touched.
rem  Use uninstall.bat to remove the module.
rem ===========================================================================
setlocal EnableDelayedExpansion

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

set "SRCDATA=%REPO%\OnyxNations\Data"
set "SRCINI=%REPO%\OnyxNations\OnyxNations.ini"
set "PROJ=%REPO%\src\OnyxNations\OnyxNations.csproj"
set "BUILT=%REPO%\src\OnyxNations\bin\Release\OnyxNations.dll"
set "SHVDNDIR=%REPO%\lib\shvdn3"

title ONYX RP - NATIONS and WAR installer

echo.
echo  ==========================================================
echo    ONYX RP: NATIONS ^& WAR   -   automatic installer
echo  ==========================================================
echo.
echo    Source folder:
echo      %REPO%
echo.

rem --- 1. are we in the right folder? ---------------------------------------
if not exist "%SRCDATA%\Nations.xml" goto :no_sources
if not exist "%SRCINI%" goto :no_sources
if not exist "%PROJ%" goto :no_sources

set "XMLCOUNT=0"
for %%F in ("%SRCDATA%\*.xml") do set /a XMLCOUNT+=1
if "%XMLCOUNT%"=="0" goto :no_sources

echo    Shipped data: %XMLCOUNT% XML file(s) + OnyxNations.ini
echo.

rem --- 2. locate the game ----------------------------------------------------
set "GTA="
set "GTALEGACY=1"
set "ARG=%~1"

if not "%ARG%"=="" (
  call :trygame "%ARG%"
  if not defined GTA (
    echo    "%ARG%" is not a GTA V folder - no GTA5.exe inside.
    echo.
  )
)

if not defined GTA (
  for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul ^| find /i "SteamPath"') do call :trygame "%%B\steamapps\common\Grand Theft Auto V"
)
if not defined GTA (
  for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Wow6432Node\Valve\Steam" /v InstallPath 2^>nul ^| find /i "InstallPath"') do call :trygame "%%B\steamapps\common\Grand Theft Auto V"
)
if not defined GTA (
  for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Wow6432Node\Rockstar Games\Grand Theft Auto V" /v InstallFolder 2^>nul ^| find /i "InstallFolder"') do call :trygame "%%B"
)
if not defined GTA for %%D in (C D E F G H) do call :scan_drive %%D

if not defined GTA (
  echo    GTA V was not found automatically.
  echo.
  echo    Type the full path of the folder that holds GTA5.exe and press Enter.
  echo    Example: D:\Games\Grand Theft Auto V
  echo    Tip: you can also drag that folder onto install.bat instead.
  echo.
  set "GTA="
  set /p "GTA=   Folder: "
  if defined GTA call :trygame "!GTA!"
  if not defined GTA goto :no_game
)

echo    Game folder:
echo      %GTA%
echo.

if "%GTALEGACY%"=="0" (
  echo    WARNING: only GTA5_Enhanced.exe was found - that is the Enhanced
  echo    edition. NATIONS ^& WAR is built for the Legacy line and will not
  echo    load there. Install it into a Legacy folder instead.
  echo.
  set "ANS="
  set /p "ANS=   Continue anyway? (y/N): "
  if /i not "!ANS!"=="y" goto :abort
)

rem --- 3. check the game folder ---------------------------------------------
echo    Checking the game folder ...
set "WARN=0"
if not exist "%GTA%\ScriptHookVDotNet3.dll" goto :no_shvdn
if exist "%GTA%\ScriptHookV.dll" ( echo      [ok]   ScriptHookV.dll ) else ( echo      [warn] ScriptHookV.dll missing - nothing .NET can load without it. & set "WARN=1" )
if exist "%GTA%\dinput8.dll" ( echo      [ok]   dinput8.dll ) else ( echo      [warn] dinput8.dll missing - no ASI loader? Onyx itself would not run. & set "WARN=1" )
if exist "%GTA%\ProjectOnyx\NUL" ( echo      [ok]   ProjectOnyx\ present ) else ( echo      [warn] ProjectOnyx\ not found - module runs self-contained. & set "WARN=1" )
if "%WARN%"=="1" (
  echo.
  set "ANS="
  set /p "ANS=   Continue anyway? (y/N): "
  if /i not "!ANS!"=="y" goto :abort
)
echo.

rem --- 4. the compile-time reference ----------------------------------------
echo    Preparing the ScriptHookVDotNet3 reference ...
if not exist "%SHVDNDIR%" mkdir "%SHVDNDIR%"
copy /y "%GTA%\ScriptHookVDotNet3.dll" "%SHVDNDIR%\ScriptHookVDotNet3.dll" >nul
if errorlevel 1 goto :copy_failed
echo      [ok]   lib\shvdn3\ScriptHookVDotNet3.dll

set "SHVDNVER="
for /f "usebackq tokens=*" %%V in (`powershell -NoProfile -Command "[System.Reflection.AssemblyName]::GetAssemblyName('%SHVDNDIR%\ScriptHookVDotNet3.dll').Version" 2^>nul`) do set "SHVDNVER=%%V"
if defined SHVDNVER echo      [ok]   version %SHVDNVER%
if defined SHVDNVER for /f "tokens=1,2 delims=." %%a in ("%SHVDNVER%") do ( set "VMAJ=%%a" & set "VMIN=%%b" )
if defined VMIN (
  if %VMIN% GEQ 9 (
    echo.
    echo      WARNING: that is the 3.9+ Enhanced runtime. NATIONS ^& WAR targets
    echo      the Legacy line ^(3.6 / 3.7^). The build may fail or the script may
    echo      not load. Get the Legacy ScriptHookVDotNet 3.6 or 3.7 instead.
    echo.
    set "ANS="
    set /p "ANS=   Continue anyway? (y/N): "
    if /i not "!ANS!"=="y" goto :abort
  )
)
echo.

rem --- 5. build --------------------------------------------------------------
echo    Building OnyxNations.dll ...
set "DOBUILD=1"
if exist "%BUILT%" (
  echo      An already built DLL exists:
  echo        %BUILT%
  set "ANS="
  set /p "ANS=   Rebuild it now? (y/N): "
  if /i not "!ANS!"=="y" set "DOBUILD=0"
)

if "%DOBUILD%"=="1" (
  call :find_msbuild
  if defined MSBUILD (
    echo      MSBuild: !MSBUILD!
    "%MSBUILD%" "%PROJ%" /p:Configuration=Release /v:minimal /nologo
    if errorlevel 1 goto :build_failed
  ) else (
    echo      no MSBuild / Visual Studio found - using the C# compiler that
    echo      ships with the .NET Framework instead. That is enough: the project
    echo      targets .NET Framework 4.8 and C# 7.3, which is exactly what
    echo      that compiler understands.
    call :build_with_csc
    if not "!CSCOK!"=="1" (
      if not exist "%BUILT%" goto :no_compiler
      echo      [warn] the compiler produced nothing - keeping the existing DLL.
    )
  )
  if not exist "%BUILT%" goto :build_failed
  echo      [ok]   built
)

set "DLLSRC=%BUILT%"
if not exist "%DLLSRC%" goto :no_dll
echo.

rem --- 6. back up what is about to be overwritten ----------------------------
set "STAMP="
for /f "tokens=1-3 delims=/-. " %%a in ("%date%") do set "STAMP=%%c%%b%%a"
for /f "tokens=1-2 delims=:., " %%a in ("%time%") do set "STAMP=%STAMP%-%%a%%b"
if not defined STAMP set "STAMP=backup"
set "STAMP=%STAMP: =0%"
set "BK=%GTA%\OnyxNations\Backup\%STAMP%"
set "BACKED=0"

call :backup "%GTA%\scripts\OnyxNations.dll" "%BK%\scripts"
call :backup "%GTA%\scripts\OnyxNations.ini" "%BK%\scripts"
call :backup "%GTA%\OnyxNations\OnyxNations.ini" "%BK%\data"
if exist "%GTA%\OnyxNations\Data" for %%F in ("%GTA%\OnyxNations\Data\*.xml") do call :backup "%%F" "%BK%\data"

rem --- 7. install ------------------------------------------------------------
echo    Installing ...
if not exist "%GTA%\OnyxNations\Data" mkdir "%GTA%\OnyxNations\Data"
if not exist "%GTA%\scripts" mkdir "%GTA%\scripts"

set "FAIL=0"
copy /y "%SRCDATA%\*.xml" "%GTA%\OnyxNations\Data" >nul
if errorlevel 1 set "FAIL=1"
copy /y "%SRCINI%" "%GTA%\scripts\OnyxNations.ini" >nul
if errorlevel 1 set "FAIL=1"
copy /y "%SRCINI%" "%GTA%\OnyxNations\OnyxNations.ini" >nul
if errorlevel 1 set "FAIL=1"
copy /y "%DLLSRC%" "%GTA%\scripts\OnyxNations.dll" >nul
if errorlevel 1 set "FAIL=1"
if "%FAIL%"=="1" goto :copy_failed

echo      [ok]   scripts\OnyxNations.dll
echo      [ok]   scripts\OnyxNations.ini
echo      [ok]   OnyxNations\Data\*.xml
echo.

rem --- 8. verify -------------------------------------------------------------
echo    Verifying ...
set "OK=0"
set "MISS=0"
call :check "%GTA%\scripts\OnyxNations.dll"
call :check "%GTA%\scripts\OnyxNations.ini"
for %%F in ("%SRCDATA%\*.xml") do call :check "%GTA%\OnyxNations\Data\%%~nxF"
echo.
echo      %OK% file(s) present, %MISS% missing.
echo.
if not "%MISS%"=="0" goto :verify_failed

echo  ==========================================================
echo    INSTALLED.
echo  ==========================================================
echo.
echo    Start the game, then open:
echo      %GTA%\OnyxNations\onyx-nations.log
echo    and look for the line:
echo      ready: 2 nations, 14 regions, 13 units
echo.
echo    In game: F8 opens the presidential tablet, Left/Right switch
echo    screens, Enter confirms the action on the screen.
echo.
if "%BACKED%"=="0" echo    Nothing was overwritten - clean install.
if not "%BACKED%"=="0" echo    %BACKED% old file(s) backed up to: %BK%
echo.
echo    Full manual walkthrough (in Russian):
echo      docs\onyx-nations\05-build-and-install.md
echo.
endlocal
pause
exit /b 0

rem ===========================================================================
rem  subroutines
rem ===========================================================================

:trygame
set "P=%~1"
if not defined P goto :eof
if exist "%P%\GTA5.exe" (
  set "GTA=%P%"
  set "GTALEGACY=1"
  goto :eof
)
if exist "%P%\GTA5_Enhanced.exe" (
  if not defined GTA (
    set "GTA=%P%"
    set "GTALEGACY=0"
  )
  goto :eof
)
goto :eof

:find_msbuild
set "MSBUILD="
set "PF86=%ProgramFiles(x86)%"
set "VSWHERE=%PF86%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :fixed_msbuild
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" 2^>nul`) do call :msb "%%i"

:fixed_msbuild
call :msb "%PF86%\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2019\Community\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2019\Professional\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2019\Enterprise\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%PF86%\Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
call :msb "%WinDir%\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe"
goto :eof

:msb
if defined MSBUILD goto :eof
if exist "%~1" set "MSBUILD=%~1"
goto :eof

rem ---------------------------------------------------------------------------
rem  Build with csc.exe out of the .NET Framework directory. Every Windows that
rem  can run GTA V has it - Visual Studio is not required to compile this
rem  project, only to feel fancy. The sources and references go into a response
rem  file because the command line would otherwise be far too long.
rem ---------------------------------------------------------------------------
:build_with_csc
set "CSCOK=0"
set "CSC="
if exist "%WinDir%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set "CSC=%WinDir%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not defined CSC if exist "%WinDir%\Microsoft.NET\Framework\v4.0.30319\csc.exe" set "CSC=%WinDir%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not defined CSC goto :eof
echo      csc: %CSC%

for %%C in ("%CSC%") do set "FWDIR=%%~dpC"
set "FWDIR=%FWDIR:~0,-1%"
set "RSP=%REPO%\src\OnyxNations\build.rsp"

if not exist "%REPO%\src\OnyxNations\bin\Release" mkdir "%REPO%\src\OnyxNations\bin\Release"
if exist "%BUILT%" del /f /q "%BUILT%"
if exist "%RSP%" del /f /q "%RSP%"

>"%RSP%"  echo /nologo
>>"%RSP%" echo /target:library
>>"%RSP%" echo /optimize+
>>"%RSP%" echo /define:TRACE
>>"%RSP%" echo /out:"%BUILT%"
>>"%RSP%" echo /reference:"%SHVDNDIR%\ScriptHookVDotNet3.dll"
for %%R in (System.dll System.Core.dll System.Drawing.dll System.Windows.Forms.dll System.Runtime.Serialization.dll System.Xml.dll System.Xml.Linq.dll) do >>"%RSP%" echo /reference:"%FWDIR%\%%R"
for /f "usebackq tokens=*" %%F in (`dir /s /b "%REPO%\src\OnyxNations\*.cs" 2^>nul ^| findstr /v /i "\\obj\\" ^| findstr /v /i "\\bin\\"`) do >>"%RSP%" echo "%%F"

"%CSC%" /langversion:7.3 @"%RSP%"
if errorlevel 1 (
  echo      [warn] /langversion:7.3 was refused - retrying with the default
  "%CSC%" @"%RSP%"
)
if exist "%BUILT%" set "CSCOK=1"
goto :eof

:scan_drive
if defined GTA goto :eof
call :trygame "%~1:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\SteamLibrary\steamapps\common\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Steam\steamapps\common\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Program Files\Epic Games\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Epic Games\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Program Files\Rockstar Games\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Rockstar Games\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Games\Grand Theft Auto V"
if defined GTA goto :eof
call :trygame "%~1:\Games\GTA V"
if defined GTA goto :eof
call :trygame "%~1:\GTA V"
goto :eof

:backup
if not exist "%~1" goto :eof
if not exist "%~2" mkdir "%~2"
copy /y "%~1" "%~2" >nul
set /a BACKED+=1
goto :eof

:check
if exist "%~1" (
  set /a OK+=1
  echo      [ok]   %~1
) else (
  set /a MISS+=1
  echo      [MISS] %~1
)
goto :eof

rem ===========================================================================
rem  errors
rem ===========================================================================

:no_sources
echo    ERROR: this is not the Project-Onyx folder. Expected to find:
echo      %SRCDATA%\Nations.xml
echo      %SRCINI%
echo      %PROJ%
echo.
echo    Run install.bat from the folder you downloaded or cloned.
echo.
pause
exit /b 1

:no_game
echo.
echo    ERROR: no GTA V folder given - nothing was installed.
echo.
pause
exit /b 1

:no_shvdn
echo    ERROR: ScriptHookVDotNet3.dll is not in the game folder.
echo.
echo      %GTA%\ScriptHookVDotNet3.dll
echo.
echo    It is the runtime that loads OnyxNations.dll, and the same file is
echo    needed to compile the project. Install ScriptHookVDotNet 3.6 or 3.7
echo    for the Legacy line first, then run install.bat again.
echo.
pause
exit /b 1

:no_compiler
echo.
echo    ERROR: no C# compiler found, and there is no built DLL.
echo.
echo    Looked for:
echo      MSBuild  (Visual Studio 2019/2022 or the free Build Tools)
echo      csc.exe  (%WinDir%\Microsoft.NET\Framework64\v4.0.30319\csc.exe)
echo.
echo    The second one is part of the .NET Framework, which GTA V and
echo    ScriptHookVDotNet already need - so if it is missing, the .NET
echo    Framework installation itself is broken. Reinstall .NET Framework 4.8
echo    and run install.bat again.
echo.
echo    Otherwise: install the free "Build Tools for Visual Studio 2022" and
echo    run this file again, or open src\OnyxNations\OnyxNations.csproj in
echo    Visual Studio, set Release and press Ctrl+Shift+B - install.bat then
echo    picks up bin\Release\OnyxNations.dll on the next run.
echo.
pause
exit /b 1

:build_failed
echo.
echo    ERROR: the build failed. The compiler output above is the reason.
echo.
echo    Nothing was installed. Copy the red error lines and send them back -
echo    the code has never been compiled by the author, so a red line is
echo    expected and easy to fix.
echo.
echo    If the error mentions ScriptHookVDotNet3, check that
echo      %SHVDNDIR%\ScriptHookVDotNet3.dll
echo    is the Legacy 3.6 / 3.7 assembly and not the Enhanced 3.9 one.
echo.
pause
exit /b 1

:no_dll
echo    ERROR: %BUILT%
echo    does not exist, so there is nothing to install.
echo.
pause
exit /b 1

:copy_failed
echo.
echo    ERROR: copying failed. Is the game folder write-protected, or is GTA
echo    still running? Close the game and try again. If the game is installed
echo    under Program Files, run this file as administrator.
echo.
pause
exit /b 1

:verify_failed
echo    ERROR: some files did not appear at the destination. See the [MISS]
echo    lines above - that is exactly what the game will complain about.
echo.
pause
exit /b 1

:abort
echo.
echo    Cancelled - nothing was changed.
echo.
pause
exit /b 1
