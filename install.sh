#!/usr/bin/env bash
set -e

if ! command -v pip &>/dev/null && ! command -v pip3 &>/dev/null; then
    echo "Error: pip not found. Please install Python 3.11+ first."
    exit 1
fi

PIP=$(command -v pip3 || command -v pip)
$PIP install -U token-tracker

echo ""
echo "Configuring status bar..."
tt setup

echo ""
echo "Done! Run 'tt' to start."
