#!/usr/bin/env bash
# =======================================================
#    DigiBuzz AI Customer Calling Agent - Auto Launcher
# =======================================================
set -e

echo "======================================================="
echo "   DigiBuzz AI Customer Calling Agent - Auto Launcher"
echo "======================================================="
echo ""

# 1. Locate Python
PY_CMD=""
if command -v python3 &> /dev/null; then
    PY_CMD="python3"
elif command -v python &> /dev/null; then
    PY_CMD="python"
else
    echo "[ERROR] Python 3 was not found in PATH."
    echo "Please install Python 3.10 or higher."
    exit 1
fi

echo "[OK] Using Python: $($PY_CMD --version)"
echo ""

# 2. Ensure .env exists
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo "[INFO] No .env file found. Creating from .env.example..."
        cp .env.example .env
        echo "[OK] Created .env configuration file."
    fi
fi

# 3. Install / Verify dependencies from requirements.txt
echo "[INFO] Installing required dependencies from requirements.txt..."
echo "(Please wait while dependencies are downloaded and verified...)"
echo ""
$PY_CMD -m pip install -r requirements.txt --disable-pip-version-check
echo ""
echo "[OK] All dependencies are ready!"
echo ""

# 4. Verify Google Gemini API Key
if ! $PY_CMD -c "import os; from dotenv import load_dotenv; load_dotenv(); exit(0 if os.getenv('GEMINI_API_KEY') and os.getenv('GEMINI_API_KEY') != 'your_primary_gemini_api_key_here' else 1)" 2>/dev/null; then
    echo "============================================================"
    echo " [SETUP] Google Gemini API Key required!"
    echo " Get your free key: https://aistudio.google.com/app/apikey"
    echo "============================================================"
    read -p "Paste Gemini API Key here (or press Enter to configure in .env): " USER_KEY
    if [ -n "$USER_KEY" ]; then
        sed -i.bak "s/^GEMINI_API_KEY=.*/GEMINI_API_KEY=$USER_KEY/" .env && rm -f .env.bak
        echo "[OK] API Key saved to .env!"
    else
        echo "[NOTE] Please edit .env and paste your GEMINI_API_KEY before calling."
    fi
    echo ""
fi

# 5. Launch Server
echo "======================================================="
echo "   Starting DigiBuzz AI Calling Agent..."
echo "   Open in browser: http://127.0.0.1:8000"
echo "   To stop server:  Press CTRL+C"
echo "======================================================="
echo ""

$PY_CMD app.py
