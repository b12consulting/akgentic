---
name: ak-framework-demo
description: Design, build and run a showcase demo of the akgentic multi-agent framework — a team that visibly collaborates rather than one agent answering alone. Use when asked to build a demo team, design a team for a business framework, fix a team whose coordinator does not delegate, or drive a live demo through the API.
argument-hint: [namespace or business framework to demo]
allowed-tools: Bash, Read, Grep, Glob, Write, Edit
---

# Akgentic Demo Builder

You are building a **showcase**: a team whose visible behaviour proves that multi-agent
collaboration produces something one well-prompted agent cannot. The deliverable is not
"a team that works" — it is **a team that visibly works as a team**.

Three failures account for nearly everything that goes wrong, and they arrive in this order:

1. **The coordinator answers alone** and never engages its members (§2.1).
2. **The team collaborates, but the output is unreadable** — a wall of text and a hairball
   graph (§2.8).
3. **A member burns the token budget** on a capability it should never have had (§2.8).

All three are visible on screen. The fourth is not: **configured guidance that is never
opened** — a `SkillTool` menu the model reads past, costing prefix on every turn and
returning nothing, with no event recording the skip (§2.6b).

Section 2 is why each happens and how to prevent it. Read it before designing anything. Every
number in this skill is measured from an event store, not estimated.

---

## 1. Read this first — the sources of truth

Read in this order. Stop when you have what you need; do not read the whole framework.

| # | Source | What it gives you |
|---|---|---|
| 1 | `CLAUDE.md` (repo root) | Golden Rules. Non-negotiable. Pydantic everywhere, no client names, no ADR strings in code. |
| 2 | `packages/akgentic-tool/README.md` § *Tool Catalog* | **The capability menu.** Every tool, its capabilities, its default channel. Read this before inventing a tool. |
| 3 | `packages/akgentic-agent/src/akgentic/agent/output_models.py` | `StructuredOutput` / `Request` — **the delegation mechanism**. The comment block at the top explains why delegation policy lives in agent prompts and not in the shared protocol. Load-bearing. |
| 4 | `packages/akgentic-agent/src/akgentic/agent/agent.py` (module + class docstring, `process_message`) | How a turn runs, how a `Request` becomes a message, the hire trap. |
| 5 | `packages/akgentic-team/src/akgentic/team/models.py` + `factory.py` | `TeamCard`: `entry_point`, `members`, `agent_profiles`, `welcome_message`. |
| 6 | `packages/akgentic-tool/src/akgentic/tool/core/params.py` | `BaseToolParam.instructions` and `expose` — per-tool guidance and channel control. |
| 6b | `packages/akgentic-tool/src/akgentic/tool/skill/README.md` | `SkillTool`: menu in the prefix, bodies on demand. Its *"the header is imperative"* section carries the ablation behind §2.6b. |
| 7 | **The live catalog export** (§4) | What is *actually running*. Never trust `data/catalog/` alone — always diff against the server. |

**Channels.** Every tool capability is exposed on one or more of `TOOL_CALL` (the LLM can
call it), `SYSTEM_PROMPT` (injected into context every turn), `COMMAND` (programmatic /
human `/`-command). `get_planning` and `get_graph` default to `SYSTEM_PROMPT` — that is
what makes them survive the no-call-stack problem.

---

## 2. Designing the demo — the dimensions that decide whether it works

### 2.1 Information asymmetry — the root cause of everything

**A coordinator that knows everything its members know will never delegate.** It is not
being lazy; delegation buys it nothing but latency. If every agent shares the same prompt
body, the same model and the same tools, you have one agent wearing five hats.

Create real asymmetry, cheapest lever first:

1. **Shard the reference material.** Split the framework/domain document so the coordinator
   holds *the method* (dimensions named, who owns what, sequencing) and each member holds
   *its own section in full*. The coordinator then **structurally cannot** answer alone.
   This is architecture, not persuasion, and it is the highest-leverage change available.
2. **Differentiate the tools via `instructions`** (§2.6). Same `SearchTool`, five different
   search stances.
3. **Differentiate stance** — a persona defending a position holds something no prompt
   sharing can transfer.
4. **Differentiate model/temperature** last. Weakest lever; use it for cost and latency.

State the asymmetry *explicitly* in the coordinator's prompt: *"You do not hold that
expertise — your specialists hold it in full and you do not. Answering a dimension question
from your own knowledge is a failure of your role, even when you believe you could."*

### 2.2 Choose what the members differ in

Two archetypes, and they teach different things. Building **both** and running the same
question through each is a stronger demo than either alone.

| Archetype | Members are… | Shows | Risk |
|---|---|---|---|
| **Division of labour** | one per method dimension | coverage, parallelism, correct routing | dimensions are rarely orthogonal in practice; members keep needing each other |
| **Stakeholder personas** | one per point of view | **disagreement** — the thing one agent cannot fake | needs personas generic enough to survive any input |

For a business framework that already names its stakeholders, the inversion to check is:
**are the framework's dimensions the *method* (coordinator's job) and the stakeholders the
*people*?** A design that turns the method into people and leaves the stakeholders
unmodelled has it backwards.

**Personas must be product-agnostic.** The user will bring a vacuum cleaner, a backpack, a
boiler or a service. Write the persona as a *role in relation to the product* (who lives
with it, who repairs it, who builds it, who sells it, who regulates it) and open each
prompt with *"work out who you are for this product, say so in one line, then speak as that
person."*

**Force disagreement structurally.** Give every persona a mandatory *"what I would refuse"*
section. A stakeholder who wants everything is useless to a board — and consensus is
comfortable and rarely informative.

### 2.3 Topology, and how to make agents actually talk to each other

**Hub-and-spoke is the safe default**: members talk only to the coordinator. Member↔member
traffic is the better showcase — an audience believes a team when it sees two agents argue —
but it needs bounding or it never terminates.

**Permission produces nothing. Only an obligation produces peer traffic.** Measured: a board
given the *right* to consult peers sent **zero** peer messages across three turns, including
one where the user asked for it in plain words. The same board with a *mandatory* dependency
sent **six per turn**. Each member could answer from its own vantage, so "consult when you
need to" never fired.

The recipe:

1. **Find a real dependency** — a class of claim agent A cannot honestly settle alone.
2. **Name exactly one peer per agent.** Not "ask whoever seems relevant".
3. **Pick a trigger that fires often.** A rare trigger is the same as no traffic.
4. **Say "consult even when you think you can guess their answer"** — a confident model skips
   an optional step. Add *"guessing is what this team exists to replace"*.
5. **Bound it three ways**, all of them:
   - at most **one** peer per task;
   - the member sends the coordinator a **`notification`** (not a position) in the same turn;
   - **"when a peer sends *you* a request, answer it and stop"** — the depth-1 cap. Without it
     a cycle of dependencies never terminates.

**Ground the dependencies in the domain if you can.** For a framework that states its own
internal dependencies, quote them — *"capabilities are reliant on composition and
connectivity"* makes @CapabilityEngineer→@Connector obvious, defensible in front of an
audience, and impossible to argue with. Invented dependencies work; derived ones persuade.

A cycle is fine (A→B→C→D→A) because the depth-1 cap terminates every chain.

### 2.4 Fan-out needs an explicit gather

There is **no call stack**. Each hop is an independent turn. A coordinator that dispatches
to five members ends its turn, then wakes five separate times — and will emit five partial
answers at the user unless you stop it.

The fix is `PlanningTool` as a ledger, wired into the workflow section of the prompt:

- one task per dispatch, created in the same turn as the dispatch;
- mark done on each reply;
- **"read the board; if any task is still open, send nothing and stop"**;
- only when the board is clear, synthesise and answer.

`get_planning` is on the `SYSTEM_PROMPT` channel, so the coordinator sees its open tasks
every turn without calling anything. That is what makes this work.

**Every new collaboration behaviour changes what "done" means.** Adding peer consultation made
members reply *twice* — a position, then an update after the peer answered — so the
coordinator closed the task on the first, reported, then reported again as updates landed.
Three reports for one round.

The fix uses the framework's existing `AgentMessage.type` vocabulary, which `REPLY_PROTOCOLS`
already puts at the top of every incoming message — no new mechanism:

- **Member:** consulting a peer → send the peer a `request` and the coordinator a
  **`notification`**, never a position. Then **exactly one `response` per task, ever**, which
  waits for the peer's answer.
- **Coordinator:** a `notification` means *still working* — do not close the task, do not
  touch the graph, do not message the user. **Only a `response` closes a task.** Report to the
  user exactly once per round.

Verified: three reports → one, and 22% cheaper because the duplicate member messages vanished.

### 2.5 The agent prompt schema

Every agent — coordinator and member — gets the same eight sections, **in this order**.
Behaviour-shaping sections first; reference material last.

| § | Section | Must contain |
|---|---|---|
| 1 | **Identity** | the literal `@Name`, because `Request.recipient` is an exact-match field |
| 2 | **Mission** | one paragraph |
| 3 | **Scope** | **the negative half carries the weight** — "You do NOT …" is what stops role bleed |
| 4 | **Input you expect** | who sends what; what to do when it is missing (ask, never invent) |
| 5 | **Output you produce** | the shape — *and* the sentence below about the messages list |
| 6 | **Communication rules** | the literal `@Names` you may send to, and the `@` warning (§2.10) |
| 7 | **Workflow** | per inbound message type; the dispatch/gather loop; **when to stop** |
| 8 | **Style** | audience register; rank last |

Two sentences that must appear near-verbatim in every prompt:

> *Everything you want read must be inside a message in your `messages` list. Text outside a
> message reaches nobody.*

> *The `recipient` must be exactly `@Name`, with the leading `@`. A recipient without a
> leading `@` silently hires a new agent instead of reaching anyone — never write a bare
> role name.*

**Termination is a required part of §7.** Nothing ends a conversation except an agent
deciding to stop. Say when to stop and hand back.

### 2.6 Per-tool `instructions` — the cheapest asymmetry there is

`BaseToolParam.instructions` appends to the **tool's docstring**, i.e. the schema the model
reads at the moment it decides whether to call. Supported on Workspace, Planning,
KnowledgeGraph, Search, Team and Notification capabilities.

Use it to give the same tool a different stance per agent:

```yaml
id_search_producer:
  kind: tool
  model_type: akgentic.tool.search.SearchTool
  payload:
    web_search:
      max_results: 5
      instructions: |
        Search for sensor and module unit prices at volume, teardowns and BOM analyses,
        radio certification requirements per market. Ground your numbers; label any
        figure you could not ground as an estimate.
```

Also use it to enforce process at point of use — e.g. on `update_graph`: *"write to it
BEFORE you reply to @Human"*; on `update_planning`: *"one task per dispatch; if any task is
open, send nothing."*

**But do not expect a docstring to make a tool get called.** It shapes *how* a tool is used
once the model has decided to reach for one. It is measurably weak at deciding *whether* —
see §2.6b, where an imperative docstring failed and a one-line system-prompt change worked.

### 2.6b `SkillTool` — an obligation only works in the frozen prefix

`SkillTool` splits a playbook by size and volatility: the **menu** (`name — description`,
one line each) goes in the frozen system prefix; the **bodies** arrive on demand as the
return value of `use_skill(name)`. It is how you give a coordinator a house method — how to
write a report, how to review a document — without paying for every playbook on every turn.

The failure mode is silent and total: **a skill the model never opens is worse than no skill
at all** — you pay for its menu line every turn and get nothing back, and no event records
that it was skipped. The absence looks exactly like a run where no skill applied.

Measured on `agent-team`, same prompt each time (*"Give me a report on Geoffroy Piroux"*),
one variable at a time:

| `MENU_HEADER` | `use_skill` description | skill loaded? |
|---|---|---|
| informational (*"call use_skill(name) to load one"*) | permissive, 70 tok | **no** |
| informational | imperative, 311 tok | **no** |
| **imperative** | permissive, unedited | **yes** |
| **imperative** | 29 tok, two sentences | **yes** |

**The trigger is the header, and nothing else substitutes for it.** The prefix is where the
model decides *how to do the work*; a tool description is read only once it has already
decided to reach for a tool — after the decision the obligation was meant to change. Two
findings fall out, and both cost nothing:

- **Key the trigger on the act, never on the topic.** The original description said to call
  it *"when the menu says a skill covers **the question** in front of you"*. No skill is
  ever *about* Geoffroy Piroux, so the model matched nothing and skipped it — correctly.
  A skill library is organised by act, so its trigger must be too.
- **State the obligation against the model's own competence.** *"even when you already know
  how"*. Without it, a capable model treats a report-writing skill as redundant, because
  writing reports is something it can already do. That is the whole point: the skill is the
  house method, not a competence top-up.

This now ships in `akgentic-tool`'s `MENU_HEADER`, so you get it by default. **What is left
to you as an operator is the per-skill `description` line** — it is the only part of the
menu you control, and it must name the *act*:

```yaml
global.id_skills_deliverables:
  kind: tool
  model_type: akgentic.tool.skill.SkillTool
  payload:
    param:
      skills:
      - name: write-report                    # the act, not the subject
        description: How to structure a report so the reader gets the answer before
          the method.
        content: |
          ...the body, delivered only when asked for...
```

Cost, for a three-skill library: header 16 → 37 tok, description 70 → 29 tok — i.e. the
working version is **~20 tokens cheaper** than the broken one. Verify it fired by looking
for a `use_skill` `ToolCallEvent` (§4); with the fix it lands *first*, before any research.

### 2.7 Correctness and legibility are separate problems

A team can collaborate perfectly and still produce something nobody can read. Measured on one
board: every fact true, and the output was a **664-word** answer with nested bullets plus a
**60-node** knowledge graph with overlapping labels. Fixing *whether* the team collaborates
does nothing about *what the user sees*. Budget a separate pass for it.

**Dialogue before dispatch.** A coordinator that convenes the whole team on the first message
is answering a question nobody asked precisely. Make step one of its workflow *"do not
dispatch, ask first"*, and name the three things it needs: the **subject**, the **context**,
and — the one that is always missing — **the decision the user is trying to make**. Cap it at
one round and three questions so it cannot interrogate; tell it to proceed on a stated
assumption if the answer is still vague. Measured: 44 words and three questions instead of a
full unsolicited answer, and it dispatches immediately when the brief is already complete.

**Hard limits, stated as limits.** *"Be concise"* does nothing. A counted ceiling mostly works:

- coordinator → user: **120 words**, no nested bullets, always close with an offer;
- member → coordinator: **150 words**, with the reason stated — *"the coordinator compresses
  further before the user sees anything, so length spent here is wasted"*.

**Word ceilings hold for the coordinator and fail for members.** Coordinators land at
93–151 against a 120 ceiling. Members run **1.5–3×** over (206, 218, 294, 300, 421 against
150), through two rounds of stronger phrasing including *"count them — a ceiling, not a
target"*. It costs tokens rather than readability, because the coordinator still compresses.
If it matters, `output_tokens_limit` on the member's `run_usage_limits` is a hard stop;
instructions are not.

**Register is a separate lever from length.** A rigid `**Agreed:** / **Split:** / **Next:**`
template reads as a form being filled in. Ask for the same beats *in prose* — *"write like a
person who has just come out of the meeting"*, *"never open a line with a bold field label"* —
and keep the same word budget. Expect prose to cost ~10-25% more words for the same content,
so cut content rather than raise the ceiling.

**Decide what the knowledge graph is for, and say so.** Two coherent settings, and the wrong
one is what produces a hairball:

| | *View* | *Record* |
|---|---|---|
| Budget | ~15 nodes | **20–30 nodes** |
| Node body | name + type | 2–3 sentences of substance, *"never a restatement of the name"* |
| Pruning | aggressively, as focus moves | **only past ~30**, and prefer merging thin nodes to deleting findings |
| Good for | a fast, legible demo beat | the artefact the user keeps |

For a *record*, give observations a fixed vocabulary and put it in the schema, the tool
`instructions` and the coordinator's prompt:

```
"Reported by @X: ..."    what that specialist established — ALWAYS present
"Confirmed by @X: ..."   a peer checked it and it held  ← the output of a consultation
"Contested by @X: ..."   a peer disagreed, unresolved — record it, never drop it
"Revised: ..."           what changed after a consultation, and why
"Evidence: ..."          a grounded external fact: a cost, a standard, a competing product
"Open: ..."              what is still unknown, and who would settle it
```

Then tell the coordinator: **after every peer consultation, record its outcome**, and grow
nodes with `update_entities` + `add_observations` rather than replacing them — *"prune nodes,
never provenance"*. Measured on one turn: 22 nodes, 23 relations, **79 observations** — 35
`Reported by`, 12 `Confirmed by`, 1 `Contested by`, 1 `Revised`, 2 `Evidence`. That is the
peer argument persisted instead of evaporating, and it is what makes a node worth clicking.

### 2.8 Give members the smallest tool surface that does the job

**This is where the money goes.** Two defaults will quietly burn a six-figure token budget,
and both were latent in every version of every team built here before they were found.

**1. Turn off `web_fetch` and `web_crawl` on members.** `SearchTool` enables all three
capabilities by default. `web_search` returns snippets; **`web_fetch` returns a full page of
extracted text — ~24k tokens in one call, and it stays in context for the rest of the turn.**
Measured input curve for one member: `[3801, 4870, 6028, 30171, 30347, …]` — the jump is the
first fetch. A specialist needs snippets.

```yaml
id_search_member:
  model_type: akgentic.tool.search.SearchTool
  payload:
    web_fetch: false
    web_crawl: false
    web_search: {max_results: 3, instructions: "At most two searches this turn…"}
```

**2. Never rely on the auto-injected `TeamTool`.** `BaseAgent` injects the **full** card when
`TeamTool` is absent from `config.tools` — so every member silently gets `hire_members`,
`fire_members` and `team_activity`. One member called **`team_activity` 23 times in a single
turn**, polling "who is working" instead of answering: 22 wasted LLM requests, 170k tokens, no
output. Give members an explicit restricted card:

```yaml
id_team_member:
  model_type: akgentic.tool.team.TeamTool
  payload:
    hire_team_members: false   # hiring is the coordinator's job
    fire_team_members: false
    get_team_activity: false   # loopable — a member polled it 23x
    get_role_profiles: false   # only needed in order to hire
    get_team_roster: true      # they DO need the exact @Names
id_team_chair:
  model_type: akgentic.tool.team.TeamTool
  payload: {}                  # coordinator keeps everything
```

Effect of the two together on the worst-behaved member: **170,146 tok / 22 req → 25,525 tok /
5 req**, and the turn completed instead of deadlocking.

Two general rules fall out:

- **A capability an agent does not need is not free** — it is an invitation to a loop.
- **Any tool that reports team or world state can be polled.** Ask of every capability you
  grant: *what happens if the model calls this in a loop?* If the answer is "nothing good",
  the member does not get it.

### 2.9 Usage limits — set both tiers before the first run

**`run_usage_limits` and `agent_usage_limits` are unset by default on `AgentConfig`.** The
only stock brake is pydantic-ai's `run_request_limit=50`, which is a loop guard, not a
budget. One agent in a search loop burned **1.1M tokens in a single run** before hitting it —
48% of a whole session's spend — and blocked the coordinator's gather permanently.

| | `run_usage_limits` (per run, pydantic-ai) | `agent_usage_limits` (lifetime, pre-flight) |
|---|---|---|
| **Coordinator** | 25 req / 15 tool calls / 250k tokens | 100 runs / 1M tokens |
| **Members** | 20 req / 12 tool calls / **150k tokens** | 50 runs / **500k tokens** |

Wire them like any other entry: `run_usage_limits: {__ref__: id_run_limits_member}`, with
`model_type: akgentic.llm.RunUsageLimits` / `akgentic.llm.AgentUsageLimits`.

Five things that cost real money to learn:

1. **The per-run `total_tokens_limit` is the only one that bounds cost.** Request and
   tool-call counts do not, because each request carries the whole growing context — 50
   requests × 22k input is 1.1M whatever the tool-call count says.
2. **Bound the coordinator first.** It is the top consumer in every team (measured
   111k–182k vs 3–53k for members). Bounding only the members fixes the wrong half.
3. **Too tight fails exactly like no limit.** A member cut off mid-work never completes its
   task, and a coordinator that waits on every task then waits for ever. Size from measured
   runs with several times headroom: a legitimate turn used 6 tool calls, a runaway 27.
4. **The framework's usage warning is a post-hoc notice, not a budget.**
   `BaseAgent.receiveMsg_AgentMessage` catches `LLMUsageLimitError`, calls `notify_human()`
   and raises `WarningError` — after the tokens are spent.
5. **Agent-tier token limits bound where a run may *start*, not end.** The run that crosses
   the line finishes; only the next is refused. Effective ceiling = limit + one run.

Check spend from the event store at any time:

```bash
python3 -c "
import json,urllib.request,collections
for t in json.load(urllib.request.urlopen('http://localhost:8000/teams'))['teams']:
    d=json.load(urllib.request.urlopen(f\"http://localhost:8000/teams/{t['team_id']}/events\"))['events']
    c=collections.Counter()
    for e in d:
        i=e['event'].get('event',{})
        if str(i.get('__model__','')).endswith('LlmUsageEvent'):
            c[(e['event'].get('sender') or {}).get('name')]+=(i.get('input_tokens',0) or 0)+(i.get('output_tokens',0) or 0)
    print(f\"{sum(c.values()):>10,}  {t['name'][:30]:32s} {dict(c.most_common(3))}\")"
```

### 2.10 Traps that will bite you

| Trap | Detail |
|---|---|
| **The hire trap** | `process_message` routes `recipient.startswith("@")` → lookup, **else → `hire_member(recipient)`**. A bare role name silently spawns a duplicate agent. Persona names (*Owner*, *Regulator*) read exactly like roles, so the risk is highest for stakeholder teams. |
| **Hired names get a suffix** | `hire_member("Challenger")` produces **`@Challenger943`**, not `@Challenger`. Expected; explain it on screen as "unique names so you can hire three of the same role". `hire_member` takes a **role**, `fire_member` takes a **name**. |
| **`agent_profiles` must not list instantiated members** | `factory.py` registers those profiles for runtime hiring. Listing a live member lets the LLM hire a duplicate by role name. Put **only** not-started agents there (that is how you make one hireable-but-absent). |
| **`routes_to` is dead code** | `AgentCard.routes_to` / `can_route_to()` exist and are unit-tested, and `factory.py` claims it wires them, but `BaseAgent.process_message()` never consults it. **There is no runtime enforcement of who may talk to whom** — the prompt's §6 is the only enforcement. |
| **`VectorStoreTool` exposes nothing** | `get_tools()` returns `[]`. It only guarantees the `#VectorStore` singleton so Planning/KnowledgeGraph can do semantic search. It is *not* a RAG surface — do not promise retrieval in a welcome message because it is in the tool list. |
| **The persistence schema must cover every output dimension** | If the team produces stakeholder/context findings but the graph schema has only structural nodes, the headline output silently persists nowhere. Extend the schema *before* the demo. |
| **A `SkillTool` menu is advisory unless the header says otherwise** | A skill the model never opens costs its menu line every turn and returns nothing, and **no event records the skip** — it is indistinguishable from a run where no skill applied. Fixed in `MENU_HEADER`; your part is naming each skill by the *act* (§2.6b). |
| **`SearchTool` enables `web_fetch`/`web_crawl` by default** | A fetch is ~24k tokens and stays in context all turn. Members want snippets. |
| **`TeamTool` is auto-injected in full** | Absent from `config.tools`, `BaseAgent` adds the whole card — members silently get hire/fire/`team_activity`, and `team_activity` is pollable (23× observed in one turn). |
| **A usage-limit failure deadlocks the gather** | The agent's task never completes, so a coordinator waiting on it waits for ever. `notify_human()` tells the human; nothing unblocks the board. |
| **`WarningMessage` is its own event type** | Not a `SentMessage`. A `SentMessage`-only filter misses every usage-limit warning. `notify_human()` is also a documented no-op when no user-proxy member remains — stopping a team suppresses the user-facing notice. |
| **`display_type` is always `other`** | On every `SentMessage`, including interim notifications. The frontend cannot style "still working" differently from a real answer. Pre-existing; a frontend change, not a team-config one. |

### 2.11 Latency — design for the room

A five-member fan-out is **1–3 minutes**, because each member runs its own searches. A
single-specialist turn is ~30s. Plan the running order so the audience earns each wait:
start with a fast single-dispatch turn, and save the full board for the finale. Keep a
pre-run process page open in a second tab as insurance — processes are event-sourced and
persist, so a finished run replays perfectly.

---

## 3. Build checklist

- [ ] Live export the reference namespace; diff against `data/catalog/` before assuming anything.
- [ ] Shard the domain document: method → coordinator, sections → members.
- [ ] Write all prompts to the §2.5 schema, negative scope included.
- [ ] Coordinator gets: read/write graph + planning + vector store.
- [ ] Members get: read-only graph + their own stance-tuned search + vector store.
- [ ] Per-tool `instructions` differ per agent.
- [ ] Extend the graph schema to cover every dimension the team outputs.
- [ ] Any `SkillTool` skill is named and described by the **act**, and a live run shows a
      `use_skill` `ToolCallEvent` firing unprompted (§2.6b).
- [ ] `agent_profiles` contains **only** hireable-but-absent agents.
- [ ] Coordinator on the stronger model; members one tier down.
- [ ] **`run_usage_limits` AND `agent_usage_limits` on every agent** (§2.9), including any
      hireable profile. Per-run `total_tokens_limit` is the one that bounds cost.
- [ ] **`web_fetch: false`, `web_crawl: false`** on every member search card (§2.8).
- [ ] **Explicit restricted `TeamTool` for members** — never the auto-injected default (§2.8).
- [ ] Coordinator's workflow step 1 is **ask, do not dispatch** (§2.8).
- [ ] Word ceilings on both tiers, and a decision on *view* vs *record* for the graph (§2.8).
- [ ] If peer traffic is wanted: a **mandatory** dependency per member, one peer each, plus the
      three bounds (§2.3), and the `notification`/`response` gather protocol (§2.4).
- [ ] `welcome_message` tells the user what to watch for, and names the agents.
- [ ] **`_meta` documents the intent** — short `description`, and `properties` carrying
      overview / principles / flow / tools / graph / limits / lineage (§4). Write it at the
      bundle level too, and read it back from the server.
- [ ] Import → validate → resolve → **live-test the actual question** (§5).

---

## 4. Interacting with the system

Community stack: backend `python src/infra_server.py` on **:8000** (**no auto-reload** —
restart to load code changes), frontend `ng serve` on **:4200**. A process page is
`http://localhost:4200/process/<team_id>`.

> Prefer `curl` against the HTTP API over the `ak-catalog` CLI when the server is up: the
> API is the state the UI actually renders. Use `ak-catalog --backend …` for offline work.

### Catalog

```bash
# What exists
curl -s http://localhost:8000/admin/catalog/namespaces

# Export a namespace as one bundle (this is the import format — round-trips)
curl -s http://localhost:8000/admin/catalog/namespace/<ns>/export -o catalog.<ns>.yaml
#   ?all=true exists and defaults to false; made no difference for team namespaces.
#   Cross-namespace refs (global.*) are included either way as read-only stubs.

# Import — ATOMIC NAMESPACE REPLACEMENT, not a merge. 201 on success.
curl -s -X POST http://localhost:8000/admin/catalog/namespace/import \
  -H "Content-Type: application/yaml" --data-binary @catalog.<ns>.yaml

# Check refs resolve — do this before creating a team
curl -s http://localhost:8000/admin/catalog/namespace/<ns>/validate     # -> {"ok":true}
curl -s http://localhost:8000/admin/catalog/team/<ns>/resolve           # full TeamCard
```

Bundle shape: `namespace`, `user_id`, `name`, `description`, `properties`, `shareable`,
`public`, then `entries:` keyed by id, each with `kind` / `model_type` / `description` /
`payload`. Include a `_meta` entry of kind `meta`. Refer to entries with
`{__ref__: <id>}`, and cross-namespace with `{__ref__: global.<id>}`.

⚠️ **The bundle header wins.** On import, `_meta` is built from the **top-level**
`namespace` / `name` / `description` / `properties` / `shareable` / `public`. A `_meta`
entry that disagrees with the header is silently ignored — a rich `properties` map written
only inside `entries._meta.payload` is dropped without an error. **Write both, identically**,
and read it back from `/admin/catalog/namespace/{ns}/meta` rather than trusting the 201.

⚠️ **Never round-trip a bundle through `yaml.safe_dump`.** It rewrites every `|` block scalar
as a quoted string with escaped newlines. The result is valid and semantically identical, and
the source becomes unreadable — every prompt in the file. If you must re-dump
programmatically, force block style back:

```python
class BlockDumper(yaml.SafeDumper): pass
def _str(d, v):
    if '\n' in v and not any(l != l.rstrip() for l in v.split('\n')):   # no trailing WS
        return d.represent_scalar('tag:yaml.org,2002:str', v, style='|')
    return d.represent_scalar('tag:yaml.org,2002:str', v)
BlockDumper.add_representer(str, _str)
yaml.dump(doc, f, Dumper=BlockDumper, sort_keys=False, allow_unicode=True, width=100)
```

Then verify nothing moved: hash `json.dumps(yaml.safe_load(path), sort_keys=True)` before and
after. Prefer targeted string edits over a full re-dump whenever you can.

#### Document the configuration inside the configuration

A namespace outlives the conversation that produced it. The next person — or you, in a month —
sees `web_fetch: false` and no reason for it, and helpfully turns it back on. **Put the intent
in `properties`**: it is a free-form `str -> str` map with no reserved keys, it travels with
export/import, and it costs nothing at runtime.

Keep `description` short — one or two sentences. It is what the namespace picker shows, and a
400-word description makes the picker unusable. Put the depth in `properties`, under these
seven keys:

| Key | What goes in it |
|---|---|
| `overview` | what this version is, and what it exists to demonstrate |
| `principles` | the design rules **and why** — the reasoning that is otherwise lost |
| `flow` | message flow, topology, gather mechanics, peer-consultation bounds |
| `tools` | every tool, **who has it and why**, and what is deliberately disabled |
| `graph` | *view* vs *record*, node budget, pruning policy |
| `limits` | both tiers, the numbers, and **what they were sized from** |
| `lineage` | predecessor and successor, so a reader knows where they are in the series |

**Write the negative choices with their evidence.** These are the ones that get undone:

> `web_fetch` and `web_crawl` are DISABLED — one fetch is ~24k tokens and stays in context
> for the whole turn.
> `TeamTool` — explicit per-tier cards, never the auto-injected default. Members get roster
> only; hire/fire and `team_activity` are coordinator-only (a member once polled
> `team_activity` 23 times in one turn instead of answering).

**Record measurements, not just values.** `limits` should say *"sized at 2.5–3× measured clean
runs (worst observed: coordinator 47,590 tokens per run, member 46,774)"*, so the next person
tuning them does not have to re-measure — and should carry the correction that
`output_tokens_limit` is a runaway guard, **not** a word-count enforcer.

**Visibility:** `GET /admin/catalog/namespaces` returns `name` / `description` / `shareable` /
`public` / `counts` but **not** `properties`. The map is reachable at
`/admin/catalog/namespace/{ns}/meta`, in the export, and in the source file — but `description`
is the only field that reaches the UI.

### Teams (processes)

```bash
curl -s -X POST http://localhost:8000/teams \
  -H "Content-Type: application/json" -d '{"catalog_namespace":"<ns>"}'      # -> team_id
curl -s http://localhost:8000/teams                                          # list
curl -s -X POST http://localhost:8000/teams/<id>/message \
  -H "Content-Type: application/json" -d '{"content":"..."}'                 # -> 204
curl -s -X POST http://localhost:8000/teams/<id>/message/<agent_name> ...    # to one agent
curl -s -X POST http://localhost:8000/teams/<id>/stop
curl -s -X DELETE http://localhost:8000/teams/<id>
```

`POST /message` returns **204 immediately** — it is fire-and-forget. All observation is via
the event stream.

### Observing a run — the event stream is the ground truth

```bash
curl -s http://localhost:8000/teams/<id>/events -o ev.json      # ?after_event_id= to tail
```

Message types: `StartMessage` (actor booted), `SentMessage` (**the one that matters** —
carries `message.recipient`, `message.type`, `message.content`), `ReceivedMessage` /
`ProcessedMessage` (the telemetry sandwich that `team_activity` derives from),
`EventMessage` (LLM usage, system prompts, tool announcements).

**The delegation check — run this first, every time:**

```bash
python3 -c "
import json
d=json.load(open('ev.json'))['events']
for e in d:
    ev=e['event']
    if not ev.get('__model__','').endswith('SentMessage'): continue
    m=ev['message']
    print((ev.get('sender') or {}).get('name'),'->',(m.get('recipient') or {}).get('name'),
          '|',m.get('type'),'|',len(m.get('content','')),'chars')
"
```

A healthy fan-out looks like: N `request` lines from the coordinator to distinct members,
one `notification` to `@Human`, then N `response` lines back, then one `response` to
`@Human`. **A single coordinator→`@Human` line with nothing in between is the failure
this skill exists to prevent.**

To read the assembled system prompt an agent actually saw (invaluable for diagnosing why it
did not delegate), find the `EventMessage` whose payload has `parts` with `dynamic_ref`
keys, and print each part's ref and length. Compare the domain-content length against the
roster length — if the ratio is 50:1, salience is your problem.

### Inspecting the knowledge graph

```bash
curl -s http://localhost:8000/teams/<id>/agent-states -o as.json
python3 -c "
t=open('as.json').read()
for k in ['Product','UseContext','Stakeholder','SubSystem','Component','Capability']:
    print(k, t.count('\"'+k+'\"'))"
```

⚠️ **Count the quoted form.** A bare `grep -c SubSystem` matches the schema text inside the
system prompt and gives a false positive on an empty graph. This has already produced one
wrong "it worked" reading.

### Waiting for a turn to finish

Never poll blind. Wait on the **specific condition**, in the background:

```bash
until [ "$(curl -s http://localhost:8000/teams/<id>/events | python3 -c '
import sys,json
d=json.load(sys.stdin)["events"]; n=0
for e in d:
  ev=e["event"]
  if ev.get("__model__","").endswith("SentMessage"):
    m=ev["message"]
    if (ev.get("sender") or {}).get("name")=="@ProductManager" \
       and (m.get("recipient") or {}).get("name")=="@Human" \
       and m.get("type")!="notification": n+=1
print(n)')" -ge 1 ]; do sleep 8; done; echo "synthesis delivered"
```

Bump the `-ge N` threshold for later turns — turn 2's synthesis is the *second* such message.

---

## 5. Verify before declaring success

Run the **real user question** through the API and check all five:

1. **Fan-out** — N distinct `request` lines from the coordinator to members.
2. **Gather** — no answer to `@Human` while tasks are open; exactly one synthesis.
3. **Attribution** — the final answer names the members (*"@X identified…"*).
4. **Tools fired** — count `update_planning` / `update_graph` / `web_search` occurrences in
   the raw events JSON.
5. **Persistence** — the graph has nodes of every type the team is supposed to produce.
6. **Spend** — total tokens for the turn, and no `WarningMessage` in the stream. A turn that
   costs an order of magnitude more than its neighbours is a loop, not a thorough agent. Check
   the per-agent `ToolCallEvent` histogram even on a healthy run: one tool dominating the count
   is a poll loop that has not yet cost you enough to notice (§7).
7. **One report per round** — the coordinator answered the user once, not once per member.
   Any behaviour that makes members reply twice (peer consultation especially) breaks the
   gather unless "done" is redefined: only a `response` closes a task, a `notification`
   means still working.

Also confirm **routing discrimination**: send a question that belongs to *one* member and
check the coordinator dispatched only to that one. Blind fan-out to everybody is a
different failure from not delegating at all, and it looks fine in a screenshot.

---

## 6. Running the demo

Suggested order — each step earns its wait:

0. **The unfixed baseline first, if you have one** — one agent answering alone, members idle.
   Ten seconds, and it makes every later contrast legible.
1. **Division-of-labour team, opening question** (~30s) → one specialist, attributed answer.
2. **Same team, a structural question** → a *different* specialist. Point at the two
   routing lines side by side: routing is a decision, not a script. Refresh the graph — it
   fills with real structure here.
3. **"Bring in the Challenger"** → a new agent is hired mid-session and kills an idea. Fast
   and dramatic; the roster grows on screen.
4. **Stakeholder board, same question** (1–3 min) → all members light up at once; the answer
   comes back with a *"where the board splits"* section.

Step 4 is the payoff. Step 3 is the thing they remember. If the room is impatient, keep a
pre-run process page open in a second tab.

**Optional:** `team_activity` is on `TeamTool` by default with no summarizer — no actor, no
model call, free. It answers *who is working right now, and on what*, which is the most
legible on-screen proof that a team is actually a team.

---

## 7. Diagnosing a run that burns tokens or hangs

**Read the `ToolCallEvent` histogram before anything else.** The token curve tells you *that*
something is wrong; only the tool counts tell you *what*. This was learned by getting it wrong
three times in a row on one agent — "too many searches" (wrong), "`web_fetch`" (real, partial),
then the actual dominant cost, `team_activity` × 23. Two limit raises happened in between,
both treating a symptom.

```bash
python3 -c "
import json,collections
d=json.load(open('ev.json'))['events']
A='@TheAgent'
tc=[i.get('tool_name') for e in d for i in [e['event'].get('event',{})]
    if str(i.get('__model__','')).endswith('ToolCallEvent')
    and (e['event'].get('sender') or {}).get('name')==A]
print(collections.Counter(tc))
print([i.get('input_tokens') for e in d for i in [e['event'].get('event',{})]
       if str(i.get('__model__','')).endswith('LlmUsageEvent')
       and (e['event'].get('sender') or {}).get('name')==A])"
```

Read the histogram and the curve together:

| Signature | Cause |
|---|---|
| one tool dominating the count | a **poll loop** — the agent is waiting by asking, not by stopping |
| a single step-change in the input curve | one **large tool result** entered context and stayed (`web_fetch`, a big graph read) |
| smooth linear growth over many requests | ordinary REACT accumulation — too many iterations, cap the tool budget |
| a `WarningMessage` and no `response` | a usage limit fired; the agent's task never completed and **the gather is now deadlocked** |

**Raising the limit is almost never the fix.** A limit firing is a report that something is
wrong. Twice here the honest fix was to remove a capability the member should never have had.
Only raise a limit when you have measured a *legitimate* turn needing more.

**When a limit is too tight it fails exactly like no limit at all** — the member's task never
completes and a coordinator waiting on every task waits for ever. Size from measured runs.

## 8. Diagnosing "the coordinator does not delegate"

Work down this list; the causes are ordered by how often they are the real one.

1. **No asymmetry** — same prompt body/model/tools everywhere. Fix by sharding (§2.1).
2. **The prompt is an answer template** — every instruction points at the user, none says
   "dispatch, then wait". Fix with §7 of the schema.
3. **The prompt points at the wrong mechanism** — e.g. "use your tools to manage the team".
   Delegation is **not** a tool; it is the `recipient` on a `Request`. `TeamTool` is
   hire/fire/roster only.
4. **Salience** — 15k chars of domain knowledge against a 300-char roster reads as "you are
   the expert, with colleagues as a footnote".
5. **Model/temperature** — an older model at low temperature in the role that needs the most
   initiative.

Check the roster was actually injected before blaming wiring: `TeamTool` is auto-injected by
`BaseAgent`, so the coordinator almost certainly *could* see its members and chose not to
use them. That makes it a design finding, not a bug — which is the more useful answer.
