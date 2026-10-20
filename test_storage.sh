#!/bin/bash

# Set environment variables for testing
export S3_ENDPOINT_URL="https://fsn1.yourdomain.com"
export S3_ACCESS_KEY="your-access-key"
export S3_SECRET_KEY="your-secret-key"

# Run the storage commands
echo "Testing storage commands..."
python3 src/cli/main.py storage list-buckets

echo ""
echo "Testing create bucket..."
python3 src/cli/main.py storage create-bucket --name test-bucket

echo ""
echo "Testing upload..."
python3 src/cli/main.py storage upload --bucket test-bucket --file README.md

echo ""
echo "Testing list objects..."
python3 src/cli/main.py storage list-objects --bucket test-bucket