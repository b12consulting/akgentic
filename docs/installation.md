# Installation

The Akgentic Framework is on PyPI. `akgentic-framework` is a
**meta-distribution**: it contains no code of its own, only a pinned set of
requirements, so an extra installs the exact subpackage versions that were
built and tested together for that release.

```bash
pip install "akgentic-framework[all]"
```

Add the optional backends and heavier tool extras (Mongo persistence, vector
search, document parsing, …):

```bash
pip install "akgentic-framework[all-extras]"
```

The base install is the actor framework alone (`akgentic.core`), so it stays a
usable minimal floor:

```bash
pip install akgentic-framework
```

## À la carte

Extras compose, and each one pins its **whole** akgentic dependency closure at
the versions of this release — so `[agent]` fixes `akgentic-llm` and
`akgentic-tool` too, rather than letting them resolve to whatever is newest.

| Extra | Installs |
|---|---|
| `core` | `akgentic-core` |
| `llm` | `akgentic-llm` |
| `tool` | `akgentic-tool` + `akgentic-core` |
| `agent` | `akgentic-agent` + `akgentic-llm`, `akgentic-tool`, `akgentic-core` |
| `team` | `akgentic-team` + `akgentic-core` |
| `catalog` | `akgentic-catalog` + `akgentic-team`, `akgentic-tool`, `akgentic-core` |
| `infra` | `akgentic-infra` + the whole set |
| `postgres` | `akgentic-catalog[postgres]`, `akgentic-team[postgres]` + their closure |

```bash
pip install "akgentic-framework[agent,catalog]"
```

`mongo` and `postgres` are mutually exclusive persistence backends, so
`[all-extras]` ships the Mongo flavour. Compose the other one explicitly:

```bash
pip install "akgentic-framework[all,postgres]"
```

Subpackages can also be installed directly — `pip install akgentic-agent` —
which is the right choice when you depend on one part and do not want a
release-wide pin.

## From a clone

Cloning this repository and syncing installs the release set from PyPI — **no
submodules needed**. This is what you want in order to run the examples, the
server boot script, or the Docker stack:

```bash
git clone https://github.com/b12consulting/akgentic-framework.git
cd akgentic-framework
uv sync
source .venv/bin/activate
```

`uv sync` installs every subpackage with its optional extras, so the demos run
immediately. (Published metadata stays lean: `pip install akgentic-framework`
still gets `akgentic.core` alone. The full set comes from a uv dependency
group, which pip ignores.)

## API keys

Every path needs an LLM provider key. The web-search tool needs a Tavily key;
without it the rest still runs.

```bash
export OPENAI_API_KEY="..."   # https://platform.openai.com/api-keys
export TAVILY_API_KEY="..."   # https://app.tavily.com/  (optional)
```

The Docker stack reads these from a `.env` file instead — see
[Run with Docker Compose](run-docker.md).

## Next

| You want to | Go to |
|---|---|
| Run the whole stack in containers | [Run with Docker Compose](run-docker.md) |
| Run the server and UI as local processes | [Run locally](run-local.md) |
| Change framework code | [Run from source](run-from-source.md) |
| Drive a team from the terminal | [Run the CLIs](run-cli.md) |
