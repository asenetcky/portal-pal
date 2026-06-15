# Open Data Portal Pal

![PyPI version](https://img.shields.io/pypi/v/open-data-portal-pal.svg)

Deployable LLM + RAG System for Interacting with Open Data Portals.

* GitHub: https://github.com/asenetcky/open-data-portal-pal/
* PyPI package: https://pypi.org/project/open-data-portal-pal/
* Created by: **[Alexander J. Senetcky](https://asenetcky.dev)** | GitHub https://github.com/asenetcky | PyPI https://pypi.org/user/asenetcky/
* Free software: MIT License

## Features

* TODO

## Documentation

Documentation is built with [Zensical](https://zensical.org/) and deployed to GitHub Pages.

* **Live site:** https://asenetcky.github.io/open_data_portal_pal/
* **Preview locally:** `just docs-serve` (serves at http://localhost:8000)
* **Build:** `just docs-build`

API documentation is auto-generated from docstrings using [mkdocstrings](https://mkdocstrings.github.io/).

Docs deploy automatically on push to `main` via GitHub Actions. To enable this, go to your repo's Settings > Pages and set the source to **GitHub Actions**.

## Development

To set up for local development:

```bash
# Clone your fork
git clone git@github.com:your_username/open-data-portal-pal.git
cd open-data-portal-pal

# Install in editable mode with live updates
uv tool install --editable .
```

This installs the CLI globally but with live updates - any changes you make to the source code are immediately available when you run `open_data_portal_pal`.

Run tests:

```bash
uv run pytest
```

Run quality checks (format, lint, type check, test):

```bash
just qa
```

## Author

Open Data Portal Pal was created in 2026 by Alexander J. Senetcky.

Built with [Cookiecutter](https://github.com/cookiecutter/cookiecutter) and the [audreyfeldroy/cookiecutter-pypackage](https://github.com/audreyfeldroy/cookiecutter-pypackage) project template.
