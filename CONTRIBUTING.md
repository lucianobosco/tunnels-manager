# Contributing

Thanks for taking a look. This is a small project with a small surface, so the rules are
short.

## Getting set up

```bash
make venv     # .venv with ruff, mypy, pytest
make check    # ruff + mypy + tests; this is what CI runs
```

`main` is protected: every change goes through a pull request, and CI has to be green
before it can be merged.

## What CI enforces

| Check | Command |
| --- | --- |
| Style and common bugs | `ruff check .` and `ruff format --check .` |
| Types | `mypy tunnels_manager` |
| Tests, 100% branch coverage of the logic | `pytest --cov` |
| No credentials or internal names | `tools/check-leaks.sh` |

Coverage is enforced at 100% for `model.py`, `config.py`, `manager.py` and `presenter.py`.
If you add a branch, add the test for it. `ui/` is thin glue on purpose: it builds widgets
and forwards events, so anything worth testing belongs in `presenter.py`, where no display
is needed.

## Nothing internal in the repository

The app is used against infrastructure that is not public. Before pushing:

```bash
git config core.hooksPath .githooks   # once, then every commit is checked
make leaks                            # or run it by hand
```

Real configurations belong in `~/.config/tunnels-manager/tunnels.yaml`, never in the repo.
Examples use invented names: `my-bastion`, `my-project-pro`. If you keep a list of names
that must never be published, put it in `.leakwords.local`, which is git-ignored.

## Commits and pull requests

- One topic per pull request, with a title that says what changes for the user.
- Explain *why* in the body when it is not obvious from the diff.
- If you touch the window, say how you checked it: the GTK layer has no automated tests.
