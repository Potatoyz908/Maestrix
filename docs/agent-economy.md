# Agent economy mode and the MX language

Several agents working together spend far more tokens than one chat: every `ask` brings the other agent's screen into the
asking agent's context, agents answer in long prose, and the Maestro polls with `check`. Economy mode cuts that traffic.

## Where to turn it on

- **Header → leaf button** (next to *Preferences*): a dropdown with **Economy mode** and, when it is on, **Agent language**.
  The leaf is grey when the mode is off and green when it is on; hovering it shows the current language.
- **Preferences → General**: the same two settings, with the same explanations.

Both write `agentEconomyMode` and `agentCompactLanguage` in `~/.open-maestri/preferences.json`. The server reads the file on
every `ask` and `check`, so a change applies at once to agents that are already running.

## What economy mode does

| | Off | On |
|---|---|---|
| Prompt sent by `ask` to an agent | as written | plus a one-line rule asking for a short answer (shells and `--raw` are untouched) |
| Reply returned by `ask` | up to 200 lines of the screen | the answer without the TUI chrome (box borders, spinners, status and shortcut bars, empty input prompt), at most 40 lines |
| `check` without a line count | last 20 lines | last 12 lines, chrome removed |

## Agent language

Only shown when economy mode is on.

- **Short replies**: agents answer in plain language, in 5 lines or fewer, and put long content (code, logs, lists) in a
  file or note, replying with its path.
- **MX telegrams**: agents talk in MX, a telegraphic key:value language. Hard for people to read, cheapest for the agents.
  Replies come back prefixed with `MX›`, which tells the asking agent to write its own requests in MX too.

### MX grammar

```
request   do:<task> in:<paths> ctx:<facts the other agent lacks> out:<shape of the reply>
reply     ok:<result> | err:<cause> | q:<question>      at most 3 lines, items split by ";"
          long content goes to a file or note and the reply carries @path
```

Example:

```
do:fix null deref login in:src/auth.py:40-60 ctx:tests/test_auth.py fails out:ok|err
ok:fixed src/auth.py:52 guard None;tests 14/14
```

The grammar is also in the `open-maestri` skill installed in every agent's skill directory.

## Where the savings come from

The savings come from sending less content: no prose, no recaps, no pasted files, no TUI chrome. Rewriting the same words
as symbols saves little, because models already encode English efficiently, and a reply the other agent misreads costs a
retry. That is why MX keeps readable keywords instead of a cipher.

Each agent still has its own base context (system prompt, `CLAUDE.md`, the files it read), so N agents cost at least N
times that. Economy mode cannot remove that cost. Two habits also help: run the agents that execute tasks on a smaller
model, and have them report back with `omaestri ask "<Maestro>" "<result>"` instead of being polled with `check`.

## Fewer wasted CLI calls

- Inside Maestrix terminals, `maestri` is an alias of `omaestri`, so an agent that loaded the Maestri skill succeeds on
  its first call instead of failing and searching for the binary.
- An unknown name in `ask` or `check` answers with the names of the connected agents, so a separate `list` call is not needed.

## Implementation

- Server: `AgentEconomy` in `Sources/InterAgent/Handlers/AgentDirectory.swift` (prompt rule, MX rule, `compact`), used by
  `AskHandler` and `CheckHandler`; `cliShimDir` in `Sources/Terminal/POSIXPtyProcess.swift` (the `maestri` alias).
- Interface: `gtk4-ui/src/ui/eco_button.py` (header leaf), `gtk4-ui/src/ui/settings_window.py` (Preferences),
  `maestri_core/preferences.py` (keys and help texts).
