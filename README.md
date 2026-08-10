<div align="center">

# Tunnels Manager

**Open your database tunnels with one click, and copy the connection string with another.**

A small GTK4 app for the tunnels you open every day: Google Cloud IAP tunnels,
`kubectl port-forward`, `ssh -L`. One window, one switch per tunnel, and the connection
details ready to paste into your SQL client.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)
![GTK 4](https://img.shields.io/badge/GTK-4-blue)
![Coverage 100%](https://img.shields.io/badge/coverage-100%25-brightgreen)

<img src="docs/screenshot.png" alt="The tunnel list with the connection panel open" width="820">

</div>

## Why

Opening a tunnel is one command. Remembering which of your fifteen local ports belongs to
which database, noticing that one of them silently died, and not leaving orphan `gcloud`
processes behind is the actual work. Tunnels Manager does that part:

- **One switch per tunnel.** The state sits right next to the switch, so you see the change
  where you are looking.
- **The port is a button.** Click it and it is on your clipboard.
- **Connection details on the side.** Host, port, a ready `mysql` command and a JDBC string,
  each with its own copy button. Web services get their URL instead.
- **It tells you when something breaks.** A tunnel that dies turns red with the reason, not
  silently keeps a dead port.
- **No orphan processes.** Closing a tunnel kills its whole process group; closing the
  window closes every tunnel.
- **Local ports stay unique**, and when one is taken the app offers to free it.

## Install

Requirements: Linux with GTK 4.12+, Python 3.11+, and `gcloud` if you use IAP tunnels.

```bash
git clone https://github.com/lucianobosco/tunnels-manager.git
cd tunnels-manager
./install.sh
```

`install.sh` checks its dependencies first and prints the exact command if something is
missing. **On a GNOME desktop there is usually nothing to install**: GTK 4 and libadwaita
are already there because the desktop itself uses them, and so are the Python bindings.
If the check does complain, on Debian or Ubuntu it is:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml
```

Those packages are the *bindings and typelibs* that let Python talk to the GTK libraries
already on your system — not GTK itself. They cannot come from `pip`: PyGObject publishes
no binary wheels, and even compiled it would still need the native libraries.

No sudo, and nothing outside your `$HOME`: a launcher in `~/.local/bin`, a desktop entry
and an icon in `~/.local/share`, and your configuration in
`~/.config/tunnels-manager/tunnels.yaml`. To uninstall, delete those four things.

Then search for **Tunnels Manager** in your launcher, or run `tunnels-manager`.

On first run the configuration is created from [`tunnels.dist.yaml`](tunnels.dist.yaml),
which is an example with made-up names. Replace those tunnels with yours.

## Use it

```
Databases
  Shop (production)      MySQL PRO   3307  ⋮  12m      ●  │  Shop (production)
  Reports (production)   MySQL PRO   3308  ⋮  error    ○  │  HOST    127.0.0.1        ⧉
  Shop (staging)   MySQL PRE 0.0.0.0 3309  ⋮  stopped  ○  │  PORT    3307             ⧉
Services                                                  │  MYSQL   mysql -h …       ⧉
  Internal dashboard     HTTP  PRO   8080  ⋮  1m       ●  │  JDBC    jdbc:mysql://…   ⧉
```

| Action | How |
| --- | --- |
| Open or close a tunnel | The switch on its row |
| Copy the local port | Click the port number |
| See host, port and connection strings | Select the row; the panel is on the right |
| Open every tunnel / close every tunnel | ▶ and ■ in the header |
| Per-tunnel actions | The ⋮ menu: view log, restart, free the port, copy, edit, delete |
| Shortcuts, new tunnel, reload | The ☰ menu |

Keyboard: `Ctrl+N` new tunnel · `Ctrl+R` reload the configuration ·
`Ctrl+I` show or hide the panel · `Ctrl+Q` quit.

### What each row tells you

| Element | Meaning |
| --- | --- |
| `MySQL` / `HTTP` | What is on the far end, which also decides the panel fields |
| `PRO` (red) / `PRE` | The environment. Colour is reserved for risk, so production stands out |
| `0.0.0.0` (amber) | The tunnel listens on every interface: anyone on your network can use it |
| `stopped` | No process |
| `opening` | The command started; the local port is not open yet |
| `12m` (green) | Open, and for how long |
| `error` (red) | It failed or died; the reason is in the tooltip and in the log |
| ⚠ | Another tunnel claims the same local port |

Column widths are fixed, so a state change never shifts the table sideways.

## Configure

Your configuration lives in `~/.config/tunnels-manager/tunnels.yaml` and is never part of
the repository. Edit it from the app (⋮ → Edit) or by hand, then press `Ctrl+R`. A running
tunnel is left alone on reload: changes apply when you restart it.

```yaml
tunnels:
  - key: shop-pro                 # internal id, must be unique
    label: Shop (production)      # what the list shows
    instance: my-bastion
    remote_port: 3306
    zone: europe-west1-d
    project: my-project-pro
    local_host: 127.0.0.1         # 0.0.0.0 to expose it to your network
    local_port: 3307
    service: mysql                # mysql | http | tcp
    database: shop                # optional: goes into the JDBC and mysql strings
    env: pro                      # optional: guessed from the project name
    extra_args: []                # optional extra gcloud flags
```

An IAP tunnel is exactly this command, nothing more:

```bash
gcloud compute start-iap-tunnel my-bastion 3306 \
  --zone=europe-west1-d --project=my-project-pro --local-host-port=127.0.0.1:3307
```

`service` does three things: sets the row badge, decides which fields the connection panel
offers, and groups the window (`mysql` → **Databases**, anything else → **Services**). Add
`group: <name>` to force a different group.

### Tunnels that are not IAP

`type: command` runs any command that opens a local port, and watches `local_port` to know
whether it is up:

```yaml
  - key: internal-dashboard
    label: Internal dashboard
    type: command
    command: kubectl -n my-namespace port-forward svc/my-dashboard 8080:80
    local_host: 127.0.0.1
    local_port: 8080
    target_label: svc/my-dashboard:80    # display only
    service: http
```

These are edited in the file: the dialog only knows about IAP tunnels.

### Shortcuts

A shortcut opens several tunnels at once from **☰ → Shortcuts**. Manage them in the app
(**☰ → Shortcuts → Manage shortcuts…**): name it and tick the tunnels. Names must be
unique and a shortcut needs at least two tunnels. Already-open tunnels are skipped, so a
tunnel shared by two shortcuts is never started twice.

```yaml
bundles:
  Daily work: [shop-pro, reports-pro]
```

## Unique local ports

Two tunnels must never share a local port: the second one to start would fail, and if you
confuse them you end up querying the wrong database. The app checks in three places:

1. **When saving** in the dialog: it refuses, names the tunnel that has the port, and
   offers the first free one.
2. **In the window**: a banner stays up while the file has duplicates, and the rows involved
   are marked with ⚠ naming the other tunnel.
3. **When starting**: it says who holds the port — another tunnel of the app, or an outside
   process — and offers to free it.

Only the port number is compared, never `local_host`: `0.0.0.0` covers `127.0.0.1`, so they
would collide anyway.

## Freeing a busy port

When a tunnel fails because its port is taken, the notification has a **Free it** button
(also in ⋮ → *Free the port and open…*). Before closing anything it asks, showing what the
process is:

```
Close the process on port 3307?

python3 · PID 48213

gcloud compute start-iap-tunnel my-bastion 3306 --zone …

              [ Cancel ]  [ Close it and open the tunnel ]
```

- If one of the app's own tunnels holds the port, nothing is killed: that tunnel is closed
  cleanly and yours is opened.
- If the process does **not** look like a tunnel (no `start-iap-tunnel`, `port-forward`,
  `cloud-sql-proxy` or `ssh`), the dialog says so: it may be something you are using.
- `SIGTERM` first, with up to 3 seconds of grace; `SIGKILL` only if the port stays busy.
- Processes owned by another user are not touched: it tells you sudo would be needed.

## Development

```bash
make venv     # create .venv with the development tools
make check    # ruff + mypy + tests with 100% coverage enforced
make test     # tests only
make leaks    # gitleaks and the private word list
```

The layout keeps the logic away from the toolkit, so almost everything is testable without
a display:

| Module | Responsibility |
| --- | --- |
| `model.py` | What a tunnel is, and what holds a local port |
| `config.py` | Reading and writing `tunnels.yaml` |
| `manager.py` | Starting, watching and killing processes |
| `presenter.py` | Every string, validation and decision the window needs |
| `ui/` | GTK widgets only: they create the widgets and forward events |

That is why `make test` runs 200+ tests in about three seconds and reports **100% coverage**
of those four modules, with no windows opening. `ui/` is deliberately thin glue and is not
part of the coverage target.

## Not leaking anything

This repository is public, but it is used against infrastructure that is not. Three
measures:

1. **The real configuration is never versioned.** It lives in
   `~/.config/tunnels-manager/tunnels.yaml`; the repo only ships `tunnels.dist.yaml` with
   invented names. `tunnels.yaml` is in `.gitignore` in case you ever copy it here.
2. **[gitleaks](https://github.com/gitleaks/gitleaks)** via `.gitleaks.toml`: the default
   rules for credentials, plus generic infrastructure patterns (GCP project ids, `kubectl`
   contexts, bastion names carrying an environment and a region).
3. **Your own list of names**, in `.leakwords.local`, which is not versioned either. Copy
   `.leakwords.local.example` and add your company, project prefixes, clusters or internal
   domains. That list lives outside the repo for a simple reason: if those names were in a
   public file, that file would be the leak.

Enable the check before every commit:

```bash
git config core.hooksPath .githooks
```

On staged changes it looks at **added** lines only, so removing an internal name is never
blocked. Over the history it looks at everything.

## License

MIT — see [LICENSE](LICENSE).
