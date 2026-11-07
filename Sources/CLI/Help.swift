// Sources/CLI/Help.swift
import Foundation

func printHelp() {
    print("""
    omaestri — open-maestri inter-agent CLI

    Usage: omaestri <command> [args...]

    Commands:
      list                              List connected agents, notes, portals
      ask "Agent" "prompt"              Send prompt to connected agent
      check "Agent" [lines]             View agent's recent output
      note <read|write|edit|create>     Read/write connected notes
      portal <subcommand>               Interact with connected portals
      recruit "Name" [--preset x]       Recruit a new agent (Maestro only)
      dismiss "Name"                    Dismiss a recruited agent
      connect "From" "To"               Connect two agents
      role <list|create|show|edit|write|assign>  Manage agent roles
      preset list                       List available agent presets
      partitura <list|create|update|insert|delete>  Saved layouts (save/apply are aliases)
      routine <list|show|create|edit|enable|disable|run|delete>  Scheduled prompts
                                        create "Name" --command "..." --every 30m [--terminal "Name"] [--disabled]
                                        (--daily/--weekly/--once/--pre-run/--reminder: not supported on Linux yet)
      workspace <list|create|rename|move|unload>  create "Name" --dir PATH; move "Name" --group G | --root
      floor <list|create|land|delete|status>  create "Name" [--from PATH] [--branch B] [--existing-branch] [--partitura P]
                                        land "Name" [--into B] [--delete-branch] [--keep-floor]
                                        delete "Name" [--keep-branch] [--keep-folder]
                                        status ["Text"] [--state S] [--progress 2/5] [--floor "Name"] [--clear]
                                        (--copy/--copy-ground/--pull-request, set-dir: not supported on Linux yet)
      debug                             Diagnose connection issues

    Environment:
      MAESTRI_SOCKET       Unix socket path (set by open-maestri)
      MAESTRI_TERMINAL_ID  Terminal UUID (set by open-maestri)
      MAESTRI_CLI          Path to this binary
    """)
}
