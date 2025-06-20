import shutil
import uuid
import json # Added import
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

# from src.server.main import app # Using isolated app
from src.server.routers.download import router as download_router # Import the specific router
from src.CodeIngest.config import TMP_BASE_PATH

# Ensure the base temporary path for digests exists before tests run
TMP_BASE_PATH.mkdir(parents=True, exist_ok=True)

# Create an isolated app for these tests
test_app = FastAPI()
test_app.include_router(download_router)

client = TestClient(test_app) # Use the isolated app

def test_download_digest_success():
    # Setup: Create a dummy digest file
    test_digest_id = str(uuid.uuid4())
    test_digest_content = "This is a test digest content."
    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_file = digest_dir / "digest.txt"
    with open(digest_file, "w") as f:
        f.write(test_digest_content)

    try:
        # Test: Make the GET request
        response = client.get(f"/download/{test_digest_id}")

        # Assertions
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8" # charset often added
        assert "attachment" in response.headers["content-disposition"]
        assert 'filename="digest.txt"' in response.headers["content-disposition"]
        assert response.text == test_digest_content
    finally:
        # Teardown: Clean up the dummy digest
        if digest_dir.exists():
            shutil.rmtree(digest_dir)

def test_download_digest_not_found():
    non_existent_digest_id = str(uuid.uuid4())
    # Requesting a .txt file by not specifying filename, or by specifying a .txt filename
    response = client.get(f"/download/{non_existent_digest_id}?filename=anyfile.txt")
    assert response.status_code == 404
    assert response.json() == {"detail": f"Digest file digest.txt not found for ID {non_existent_digest_id}."}

def test_download_digest_success_with_custom_filename():
    test_digest_id = str(uuid.uuid4())
    test_digest_content = "Custom filename test."
    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_file = digest_dir / "digest.txt"
    with open(digest_file, "w") as f:
        f.write(test_digest_content)

    custom_filename = "my_special_digest.txt"
    try:
        response = client.get(f"/download/{test_digest_id}?filename={custom_filename}")
        assert response.status_code == 200
        assert f'filename="{custom_filename}"' in response.headers["content-disposition"]
        assert response.text == test_digest_content
    finally:
        if digest_dir.exists():
            shutil.rmtree(digest_dir)

def test_download_digest_with_invalid_filename_query():
    test_digest_id = str(uuid.uuid4())
    test_digest_content = "Invalid filename test."
    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_file = digest_dir / "digest.txt"
    with open(digest_file, "w") as f:
        f.write(test_digest_content)

    invalid_filenames = ["", "nodotextension", "../../../etc/passwd.txt"]
    try:
        for invalid_fn in invalid_filenames:
            response = client.get(f"/download/{test_digest_id}?filename={invalid_fn}")
            assert response.status_code == 200
            assert 'filename="digest.txt"' in response.headers["content-disposition"] # Should default
            assert response.text == test_digest_content
    finally:
        if digest_dir.exists():
            shutil.rmtree(digest_dir)

def test_download_digest_file_missing_in_dir():
    test_digest_id = str(uuid.uuid4())
    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True) # Directory exists, file does not

    try:
        # Requesting a .txt file by not specifying filename, or by specifying a .txt filename
        response = client.get(f"/download/{test_digest_id}?filename=some_file.txt")
        assert response.status_code == 404
        assert response.json() == {"detail": f"Digest file digest.txt not found for ID {test_digest_id}."}
    finally:
        if digest_dir.exists():
            shutil.rmtree(digest_dir)

def test_download_json_digest_success():
    test_digest_id = str(uuid.uuid4())
    test_data = {"key": "value", "message": "hello json"}
    test_digest_content = json.dumps(test_data, indent=2)

    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_file = digest_dir / "digest.json" # Save as digest.json
    with open(digest_file, "w") as f:
        f.write(test_digest_content)

    requested_filename = "my_output.json"
    try:
        response = client.get(f"/download/{test_digest_id}?filename={requested_filename}")

        assert response.status_code == 200
        # Content-Type for application/json might not include charset by default with FileResponse
        assert response.headers["content-type"] == "application/json"
        assert "attachment" in response.headers["content-disposition"]
        assert f'filename="{requested_filename}"' in response.headers["content-disposition"]
        assert response.json() == test_data # Compare parsed JSON
    finally:
        if digest_dir.exists():
            shutil.rmtree(digest_dir)

def test_download_json_digest_file_missing_in_dir():
    test_digest_id = str(uuid.uuid4())
    digest_dir = TMP_BASE_PATH / test_digest_id
    digest_dir.mkdir(parents=True, exist_ok=True) # Directory exists, digest.json does not

    requested_filename = "data.json"
    try:
        response = client.get(f"/download/{test_digest_id}?filename={requested_filename}")
        assert response.status_code == 404
        assert response.json() == {"detail": f"Digest file digest.json not found for ID {test_digest_id}."}
    finally:
        if digest_dir.exists():
            shutil.rmtree(digest_dir)
