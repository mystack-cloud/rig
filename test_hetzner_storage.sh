#!/bin/bash

# Set environment variables for testing
export HZ_TOKEN="test-hetzner-token-12345"
export S3_ENDPOINT_URL="https://fsn1.testdomain.com"

echo "Testing Hetzner S3-compatible storage CLI..."

echo ""
echo "1. Testing list buckets..."
python3 src/cli/main.py storage list-buckets

echo ""
echo "2. Testing create bucket..."
python3 src/cli/main.py storage create-bucket --name test-bucket

echo ""
echo "3. Testing upload..."
python3 src/cli/main.py storage upload \
  --bucket test-bucket \
  --file README.md \
  --object-name readme-copy.md

echo ""
echo "4. Testing list objects..."
python3 src/cli/main.py storage list-objects \
  --bucket test-bucket

echo ""
echo "5. Testing delete object..."
python3 src/cli/main.py storage delete-object \
  --bucket test-bucket \
  --object readme-copy.md

echo ""
echo "All tests completed!"