# a-thousand-blank-white-cards

A digital implementation of the party game 1000 Blank White Cards, where players draw, write, and play their own cards to invent the game as they go.


## Setup

This project uses `uv` for dependency management.

1.  **Install `uv`**:
    Follow the instructions at [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/).
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2.  **Install Python**:
    `uv` manages Python versions for you.
    ```bash
    uv python install
    ```

3.  **Install Dependencies**:
    ```bash
    uv sync
    ```


## Contributing

### Setup Pre-Commit (Optional)

All linting and testing is done in CI, however, if you want to run them locally, you can use prek (preferred) or pre-commit as documented below.

```bash
uvx prek install
```

### Running Tests
Run the test suite using `pytest`. This will also check that code coverage is at least 80%.
```bash
uv run pytest
```

### Linting and Formatting
Check for linting errors using `ruff`:
```bash
uv run ruff check
```

Format code:
```bash
uv run ruff format
```
