# Working on this repository

Tunnels Manager is a GTK4 desktop app that opens and closes local port tunnels (Google
Cloud IAP, `kubectl port-forward`, `ssh -L`) and hands you the connection details. It is
small on purpose. Keep it that way.

## The one architectural rule

**The logic never imports GTK.** Anything that computes a string, validates a form, or
decides what to show belongs outside the toolkit:

| Module | Responsibility | Imports GTK? |
| --- | --- | --- |
| `tunnels_manager/model.py` | What a tunnel is; who holds a local port | No |
| `tunnels_manager/config.py` | Reading and writing `tunnels.yaml` | No |
| `tunnels_manager/manager.py` | Starting, watching and killing processes | No |
| `tunnels_manager/presenter.py` | Every string, validation and decision the window needs | No |
| `tunnels_manager/ui/` | Builds widgets, forwards events | Yes, only here |

If you are about to write an `if` inside a widget class, it probably belongs in
`presenter.py` as a function that takes parameters and returns a value.

**Do not write tests that build widgets.** An earlier version of this repo had a GTK test
suite; it opened real windows on the developer's screen and was deleted. The UI is thin
glue and is deliberately outside the coverage target. Test the presenter instead.

## Commands

```bash
make venv     # .venv with ruff, mypy, pytest (uses --system-site-packages for PyGObject)
make check    # ruff + mypy + tests. This is exactly what CI runs
make test     # tests only, 100% branch coverage enforced
make leaks    # gitleaks plus the private word list
```

`make check` has to pass before you open a pull request. Coverage is enforced at **100%**
for `model.py`, `config.py`, `manager.py` and `presenter.py`: if you add a branch, add the
test for it.

## Tests

- Fixtures live in `tests/conftest.py`. Every test that touches configuration gets its own
  `XDG_CONFIG_HOME`, so the developer's real `tunnels.yaml` is never read or written. Never
  bypass that.
- Child processes in tests must use `start_new_session=True`. The manager kills process
  *groups*, and a helper sharing pytest's group takes the test runner down with it.
- Prefer a real child process over a mock when the point is process behaviour; use
  `FakeProc` when the point is a branch.
- Never patch `os.kill` globally: use the `TunnelManager.signal_pid` seam.

## Nothing internal in the repository

The app is used against infrastructure that is not public.

- Real configuration lives in `~/.config/tunnels-manager/tunnels.yaml`. It is git-ignored
  and must never be committed, quoted in a comment, or pasted into a test.
- Examples use invented names: `my-bastion`, `my-project-pro`, ports in the 3300 range.
- `.gitleaks.toml` only carries *generic* patterns. Concrete names go in
  `.leakwords.local`, which is git-ignored — if those names were in a public file, that
  file would be the leak.
- Screenshots for the README must be taken with the example configuration, never with real
  tunnels.
- Run `make leaks` before pushing. The pre-commit hook does it for staged changes
  (`git config core.hooksPath .githooks`).

## Style

- Everything in English: code, comments, docstrings, interface strings, commit messages.
- Line length 100. `ruff format` decides the rest; do not argue with it.
- Comments explain *why*, not what. If a line needs a comment to say what it does, rename
  something instead.
- User-facing strings say what happens and, when something failed, how to fix it. No
  apologies, no vagueness.
- Dependencies: PyYAML, and PyGObject from the distribution. Adding a third one needs a
  reason in the pull request.

## Pull requests

`main` is protected: no direct pushes, CI must be green, linear history, squash merges.

```bash
git checkout -b topic/what-it-does
make check
git push -u origin topic/what-it-does
gh pr create
```

One topic per pull request. Title says what changes for the user. If you touched `ui/`, say
in the body how you checked it by hand, because nothing automated covers it.
