import os
from pathlib import Path

root = Path(r"C:\Users\yassi\OneDrive\Документы\rgpd-multi-agent")
total = 0
files_count = 0

for py_file in root.rglob("*.py"):
    if "venv" not in str(py_file):
        try:
            with open(py_file, encoding="utf-8") as f:
                lines = len(f.readlines())
                total += lines
                files_count += 1
        except:
            pass

print(f"Files: {files_count}")
print(f"Total lines: {total}")