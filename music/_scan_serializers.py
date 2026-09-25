# -*- coding: utf-8 -*-
import io, re
path = r"C:\Users\caiom\Documents\idbfinance\backend\music\serializers.py"
lines = io.open(path, encoding="utf-8").read().splitlines()
patterns = [
    "def get_can_edit", "def get_can_delete", "can_edit =", "can_delete =",
    "def get_can_manage", "SETLIST_GOVERNANCE", "get_can_create",
    "def get_created_by_name", "def validate(self", "BandSetlistSerializer) or ",
    "def get_band_name", "class BandSetlistSerializer",
]
for i, l in enumerate(lines):
    for p in patterns:
        if p in l:
            print(f"{i+1}: {l}")
            break
