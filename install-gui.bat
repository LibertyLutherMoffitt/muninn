@echo off
setlocal

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Installing Muninn GUI...
pip install "muninn[gui] @ git+https://github.com/LibertyLutherMoffitt/muninn.git#subdirectory=python"
if %errorlevel% neq 0 (
    echo.
    echo Install failed. Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo Launching Muninn GUI...
muninn-gui
