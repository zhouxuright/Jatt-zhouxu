#!/bin/sh
echo "=== tesseract check ==="
which tesseract || echo "no tesseract binary"
tesseract --version 2>&1 | head -2 || true

echo "=== ddddocr install to /tmp/ddddocr_lib ==="
pip install ddddocr --target=/tmp/ddddocr_lib --quiet --no-warn-script-location 2>&1 | tail -3

echo "=== ddddocr import test ==="
python - <<'PYEOF'
import sys
sys.path.insert(0, "/tmp/ddddocr_lib")
try:
    import ddddocr
    print("ddddocr import OK")
except Exception as e:
    print(f"ddddocr import FAILED: {e}")
PYEOF
