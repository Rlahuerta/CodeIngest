# CodeIngest

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Rlahuerta/CodeIngest/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Rlahuerta/CodeIngest?style=social.svg)](https://github.com/Rlahuerta/CodeIngest)

Turn any Git repository (remote URL or local path) into a prompt-friendly text digest for LLMs.


Turn any Git repository into a prompt-friendly text ingest for LLMs.

You can also replace `hub` with `ingest` in any GitHub URL to access the corresponding digest.

[gitingest.com](https://gitingest.com) · [Chrome Extension](https://chromewebstore.google.com/detail/adfjahbijlkjfoicpjkhjicpjpjfaood) · [Firefox Add-on](https://addons.mozilla.org/firefox/addon/gitingest)

## 🚀 Features

- **Easy Code Context**: Get a text digest from a Git repository URL or a local directory path.
- **Branch/Tag/Commit Support**: Specify a particular branch, tag, or commit hash when ingesting a remote repository URL.
- **Smart Formatting**: Optimized output format for LLM prompts.
- **Statistics**: Provides details on file/directory structure and estimated token count.
- **CLI Tool**: Run `codeingest` as a shell command.
- **Python Package**: Import `CodeIngest` in your Python code.

## 📚 Requirements

- Python 3.10+
- Git installed on your system

### 📦 Installation

`CodeIngest` is intended for local use or self-hosting. If published to PyPI (not yet), you could install it via pip:

```bash
pip install CodeIngest
```

## For development or local use, clone the repository and install using Poetry:
```bash
git clone [https://github.com/Rlahuerta/CodeIngest.git](https://github.com/Rlahuerta/CodeIngest.git)
cd CodeIngest
poetry install
```

Using `pipx` is also a good option for installing Python CLI tools in isolation:

```bash
brew install pipx
apt install pipx
scoop install pipx
...
```

If you are using pipx for the first time, run:

```bash
pipx ensurepath
```

```bash
# install CodeIngest
pipx install CodeIngest
```

## 💡 Command line usage

The `codeingest` command line tool allows you to analyze codebases and create a text dump of their contents.

```bash
# Ingest a local directory
codeingest /path/to/your/local/repo

# Ingest the current directory
codeingest .

# Ingest from a remote URL (default branch)
codeingest [https://github.com/tiangolo/fastapi](https://github.com/tiangolo/fastapi)

# Ingest from a remote URL specifying a branch/tag/commit
codeingest [https://github.com/pallets/flask](https://github.com/pallets/flask) -b 2.3.x

# Specify output file and exclude patterns
codeingest . -o my_digest.txt -e "*.log" -e "dist/"

# See more options
codeingest --help
```

This will write the digest in a text file (default `digest.txt` or `project_name_branch.txt`) in your current working directory.

## 🐍 Python package usage

```python
# Synchronous usage
from CodeIngest import ingest

# Analyze a local directory
summary, tree, content = ingest("/path/to/local/repo")

# Analyze from URL (default branch)
summary_url, tree_url, content_url = ingest("[https://github.com/Rlahuerta/CodeIngest](https://github.com/Rlahuerta/CodeIngest)")

# Analyze from URL with specific branch and output file
summary_branch, _, _ = ingest("[https://github.com/pallets/flask](https://github.com/pallets/flask)", branch="2.3.x", output="flask_digest.txt")

print(summary)
```

By default, this won't write a file but can be enabled with the `output` argument.

```python
# Asynchronous usage
from CodeIngest import ingest_async
import asyncio

async def run_analysis():
    # Analyze local directory asynchronously
    result_local = await ingest_async("/path/to/local/repo")

    # Analyze URL asynchronously
    result_url = await ingest_async("https://github.com/Rlahuerta/CodeIngest", branch="main")

    print(result_local[0]) # Print summary
    print(result_url[0])   # Print summary

# asyncio.run(run_analysis())
```

### Jupyter notebook usage

```python
# In a Jupyter cell
from CodeIngest import ingest_async

# Use await directly in Jupyter (which runs its own event loop)
summary, tree, content = await ingest_async("/path/to/local/repo")
# summary_url, _, _ = await ingest_async("https://github.com/Rlahuerta/CodeIngest", branch="main")

print(summary)
```

This is because Jupyter notebooks are asynchronous by default.

## 🐳 Self-host

You can run the included FastAPI web interface locally using Docker.

1. Build the image:

   ``` bash
   docker build -t codeingest .
   ```
   
   or via Docker Compose

   ``` bash
   docker compose up --build
   ```

2. Run the container:

   ``` bash
   docker run -rm -d --name codeingest -p 8000:8000 codeingest:latest
   ```

The application will be available at `http://localhost:8000`.

If you are hosting it on a domain, you can specify the allowed hostnames via env variable `ALLOWED_HOSTS`.

   ```bash
   # Default: "CodeIngest.com, *.CodeIngest.com, localhost, 127.0.0.1".
   ALLOWED_HOSTS="example.com, localhost, 127.0.0.1"
   ```

*Security Warning:* Enabling local path processing in the web interface is highly insecure if the server is exposed. Use only in trusted, isolated environments.

## 🤝 Contributing

Contributions are welcome! Please refer to `CONTRIBUTING.md` for details on how to set up the development environment and submit pull requests.

If you find a bug or have a feature request, please create an issue on GitHub.

## 🛠️ Stack
- [FastAPI](https://github.com/fastapi/fastapi) - Backend framework
- [Uvicorn](https://www.uvicorn.org/) - ASGI server
- [Jinja2](https://jinja.palletsprojects.com/en/stable/) - HTML templating
- [Tailwind CSS](https://tailwindcss.com/) - Frontend styling (via CDN)
- [Click](https://click.palletsprojects.com/en/stable/) - CLI framework
- [tiktoken](https://github.com/openai/tiktoken) - Token estimation
- [Poetry](https://python-poetry.org/) - Dependency management
- [Pytest](https://docs.pytest.org/en/stable/) - Testing framework
- [pytest-cov](https://pytest-cov.readthedocs.io/en/latest/) - Coverage reporting