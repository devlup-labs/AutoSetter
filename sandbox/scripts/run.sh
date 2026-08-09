#!/bin/bash

# Check if a filename is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <filename.cpp>"
    exit 1
fi

FILE=$1
# Extract filename without extension for the executable
FILENAME="${FILE%.*}"

echo "========================================"
echo "Compiling $FILE..."
echo "========================================"

# Measure compilation time
time g++ -O3 -o "$FILENAME" "$FILE"
COMPILE_STATUS=$?

if [ $COMPILE_STATUS -ne 0 ]; then
    echo -e "\n[Error] Compilation failed!"
    exit 1
fi

echo -e "\n========================================"
echo "Executing $FILENAME..."
echo "========================================"

# Check if input.txt exists to pass as standard input
if [ -f "input.txt" ]; then
    time ./"$FILENAME" < input.txt
else
    # If no input.txt, run normally (will wait for manual input if required)
    time ./"$FILENAME"
fi

echo "========================================"
