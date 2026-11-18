@echo off
rem ===========================================================================
rem  ONYX RP: NATIONS ^& WAR - uninstaller
rem
rem  Removes the module files that install.bat put in the game folder:
rem    <GTA>\scripts\OnyxNations.dll
rem    <GTA>\scripts\OnyxNations.ini
rem    <GTA>\OnyxNations\OnyxNations.ini
rem    <GTA>\OnyxNations\Data\*.xml
rem
rem  It does NOT touch, unless you explicitly say so at the end:
rem    <GTA>\OnyxNations\Saves\   (your saved worlds)
rem    <GTA>\OnyxNations\Backup\  (files install.bat backed up)
rem    <GTA>\OnyxNations\onyx-nations.log
rem
rem  Nothing belonging to Onyx RP itself is ever modified.
rem
rem  Usage:
rem    uninstall.bat                       auto-detect the game folder
rem    uninstall.bat "D:\Games\GTA V"      use this folder
rem ===========================================================================
setlocal EnableDelayedExpansion

title ONYX RP - NATIONS and WAR uninstaller

echo.
echo  ==========================================================
echo    ONYX RP: NATIONS ^& WAR   -   uninstaller
echo  ==========================================================
echo.

rem --- 1. locate the game ----------------------------------------------------
set "GTA="
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
  echo.
  set "GTA="
  set /p "GTA=   Folder: "
  if defined GTA call :trygame "!GTA!"
  if not defined GTA goto :no_game
)

echo    Game folder:
echo      %GTA%
echo.

rem --- 2. is it even installed? ----------------------------------------------
if not exist "%GTA%\scripts\OnyxNations.dll" (
  if not exist "%GTA%\OnyxNations\NUL" goto :not_installed
  echo    [warn] scripts\OnyxNations.dll is already gone - cleaning the rest.
)
echo.

rem --- 3. remove -------------------------------------------------------------
echo    Removing ...
set "REMOVED=0"

if exist "%GTA%\scripts\OnyxNations.dll" (
  del /f /q "%GTA%\scripts\OnyxNations.dll" >nul 2>&1
  if not exist "%GTA%\scripts\OnyxNations.dll" (
    echo      [gone] scripts\OnyxNations.dll
    set /a REMOVED+=1
  ) else (
    echo      [FAIL] scripts\OnyxNations.dll - close the game and retry.
  )
)

if exist "%GTA%\scripts\OnyxNations.ini" (
  del /f /q "%GTA%\scripts\OnyxNations.ini" >nul 2>&1
  if not exist "%GTA%\scripts\OnyxNations.ini" (
    echo      [gone] scripts\OnyxNations.ini
    set /a REMOVED+=1
  ) else (
    echo      [FAIL] scripts\OnyxNations.ini - close the game and retry.
  )
)

if exist "%GTA%\OnyxNations\OnyxNations.ini" (
  del /f /q "%GTA%\OnyxNations\OnyxNations.ini" >nul 2>&1
  if not exist "%GTA%\OnyxNations\OnyxNations.ini" (
    echo      [gone] OnyxNations\OnyxNations.ini
    set /a REMOVED+=1
  )
)

if exist "%GTA%\OnyxNations\Data\NUL" (
  del /f /q "%GTA%\OnyxNations\Data\*.xml" >nul 2>&1
  set /a REMOVED+=1
  echo      [gone] OnyxNations\Data\*.xml
  rd "%GTA%\OnyxNations\Data" >nul 2>&1
)
echo.

rem --- 4. what is left -------------------------------------------------------
if not exist "%GTA%\OnyxNations\NUL" goto :done_clean

echo    Kept in %GTA%\OnyxNations\ :
for /f "tokens=*" %%L in ('dir /b "%GTA%\OnyxNations" 2^>nul') do echo      %%L
echo.
echo    Your saved worlds are untouched. To delete everything, including
echo    saves and backups, delete that folder yourself or answer yes below.
echo.
set "ANS="
set /p "ANS=   Delete <GTA>\OnyxNations\ completely, saves included? (y/N): "
if /i "!ANS!"=="y" (
  rd /s /q "%GTA%\OnyxNations"
  if not exist "%GTA%\OnyxNations\NUL" (
    echo      [gone] OnyxNations\
  ) else (
    echo      [FAIL] could not delete it - close the game and retry.
  )
)
echo.

:done_clean
echo  ==========================================================
echo    UNINSTALLED.
echo  ==========================================================
echo.
if not exist "%GTA%\OnyxNations\NUL" echo    %GTA%\OnyxNations\ is gone - nothing of the module remains.
echo.
echo    Onyx RP itself was not touched. Reinstall any time with install.bat.
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
  goto :eof
)
if exist "%P%\GTA5_Enhanced.exe" (
  if not defined GTA set "GTA=%P%"
  goto :eof
)
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

rem ===========================================================================
rem  errors
rem ===========================================================================

:no_game
echo.
echo    ERROR: no GTA V folder given - nothing was removed.
echo.
pause
exit /b 1

:not_installed
echo    NATIONS ^& WAR does not appear to be installed in that folder:
echo      %GTA%\scripts\OnyxNations.dll  -  missing
echo      %GTA%\OnyxNations\             -  missing
echo.
echo    Nothing to do.
echo.
pause
exit /b 1
