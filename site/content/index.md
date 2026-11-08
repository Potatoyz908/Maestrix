---
title: Maestrix
layout: landing
description: Multi-agent AI orchestration canvas for Linux.
---
<section class="hero">
  <img class="logo" src="assets/icon.png" alt="Maestrix">
  <h1>Maestrix</h1>
  <p class="tag">The multi-agent AI orchestration canvas for Linux. Manage agents like a team, not a pile of terminals.</p>
  <div class="cta">
    <a class="btn primary" href="getting-started.html">Get started</a>
    <a class="btn" href="concepts.html">How it works</a>
    <a class="btn" href="https://github.com/Potatoyz908/Maestrix">GitHub</a>
  </div>
  <div class="shot"><img src="assets/img/home-dark.png" alt="Maestrix in the Dark theme"></div>
</section>

<section class="section">
  <h2>One canvas for every agent</h2>
  <p class="lead">Maestrix is not an agent. It is the workspace around the agents you already use: Claude Code, Codex, Gemini CLI, Antigravity, OpenCode, or a plain shell.</p>
  <div class="grid">
    <a class="card" href="canvas-and-nodes.html"><span class="ico">◧</span><h3>Infinite canvas</h3><p>Terminals, Markdown notes, file trees, browsers, labels and drawings, joined by physics-based ropes.</p></a>
    <a class="card" href="terminals-and-agents.html"><span class="ico">›_</span><h3>Real terminals</h3><p>VTE terminals owned by a background server, so a workspace switch never kills a running shell.</p></a>
    <a class="card" href="agent-communication.html"><span class="ico">⇄</span><h3>Agents talk to agents</h3><p><code>omaestri ask</code> lets connected agents delegate and answer, with no human router in between.</p></a>
    <a class="card" href="agent-communication.html#maestro-mode"><span class="ico">♪</span><h3>Maestro Mode</h3><p>A lead agent recruits, connects, briefs and dismisses teammates by itself.</p></a>
    <a class="card" href="floors.html"><span class="ico">⎇</span><h3>Floors</h3><p>Isolated git worktrees per task, with setup, run and teardown hooks, and a Land action.</p></a>
    <a class="card" href="remote-ssh.html"><span class="ico">⌁</span><h3>Remote SSH</h3><p>Open a workspace whose terminals run on a remote machine, in the folder you pick.</p></a>
  </div>
</section>

<section class="section">
  <h2>Delegate from the command line</h2>
  <p class="lead">Every connected terminal gets the <code>omaestri</code> CLI. Agents see their neighbours and hand work over directly.</p>
  <div class="split">
    <div class="hl"><pre><span></span>omaestri list
omaestri ask "Reviewer" "Review my PR"
omaestri check "Builder"
omaestri note write "Spec" "content"
omaestri recruit "Helper" --preset claude_code</pre></div>
    <div>
      <p>Permissions follow the ropes you draw. Two terminals that are not connected cannot see each other.</p>
      <p><a href="cli-reference.html">Read the CLI reference →</a></p>
    </div>
  </div>
</section>

<section class="section">
  <h2>Dark by default, light when you want it</h2>
  <p class="lead">The interface theme follows the app icon: navy ground, a blue to purple gradient and cyan highlights.</p>
  <div class="grid">
    <div class="shot" style="margin:0;padding:0"><img src="assets/img/agents-dark.png" alt="Dark theme: terminals, a note and a file tree"></div>
    <div class="shot" style="margin:0;padding:0"><img src="assets/img/home-light.png" alt="Light theme"></div>
  </div>
</section>

<section class="section">
  <h2>Documentation</h2>
  <div class="grid">
    <a class="card" href="getting-started.html"><h3>Getting started</h3><p>Install the <code>.deb</code> or build from source, then create your first workspace.</p></a>
    <a class="card" href="configuration.html"><h3>Configuration</h3><p>Data directory, preferences, environment variables and the files Maestrix writes.</p></a>
    <a class="card" href="troubleshooting.html"><h3>Troubleshooting</h3><p>Offline mode, a missing server, stale sockets and agents that cannot reach each other.</p></a>
    <a class="card" href="development.html"><h3>Development</h3><p>Build, run the tests, repository layout and packaging.</p></a>
  </div>
</section>
