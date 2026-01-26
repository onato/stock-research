#!/bin/bash

# GOOGL Financial Metrics Extraction Script
# Run this to extract metrics from all SEC filings

echo "=========================================="
echo " GOOGL Metrics Extraction"
echo "=========================================="
echo ""

cd "/Users/swilliams/Stocks/Research/GOOGL"

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found"
    echo "Please install Python 3 first"
    exit 1
fi

echo "Running extraction..."
echo ""

# Run the extractor
python3 final_extract.py

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo " Extraction Complete!"
    echo "=========================================="
    echo ""
    echo "Output file: GOOGL/Reports/GOOGL_Metrics.csv"
    echo ""

    # Display first few lines of CSV
    if [ -f "Reports/GOOGL_Metrics.csv" ]; then
        echo "First 10 rows of CSV:"
        echo "------------------------------------------"
        head -11 Reports/GOOGL_Metrics.csv | column -t -s,
        echo ""
        echo "Last 10 rows of CSV:"
        echo "------------------------------------------"
        tail -10 Reports/GOOGL_Metrics.csv | column -t -s,
    fi
else
    echo ""
    echo "Error during extraction"
    exit 1
fi
