# Codex Context GC

**Candidate browser repair:** the `signed-runtime-adapter` branch includes an experimental adapter that keeps the original signed CLI. See [SIGNED-RUNTIME.md](SIGNED-RUNTIME.md) for tests, launch instructions, and the remaining live desktop verification. The original installer below still builds the custom CLI.

**Let the model compact at sensible work boundaries, instead of waiting for the context window to fill.**

This small, experimental patch adds a `compact_context` tool to Codex. The model records what it has learned, asks Codex to run its native compaction, and continues the same task with those notes intact.

**Known macOS desktop incompatibility:** the custom-built CLI can be rejected by the in-app browser and dynamic app-tools bridges with `missing-code-signing-identity`. This is reproduced in the author's desktop logs. The published build is **not fully desktop-compatible**. If you need these integrations, keep using the stock desktop executable. See [browser compatibility](#browser-compatibility) below.

Inspired by [pi-context-gc](https://github.com/manuelcecchetto/pi-context-gc). Unofficial; not an OpenAI product.

## Install: ask Astra

Paste this into a Codex task using Astra:

> Install https://github.com/manuelcecchetto/codex-context-gc for my local Codex desktop app. Read its README.md and INSTALL.md, check compatibility, and use its included compaction instructions. Preserve my existing setup and instructions. Build and test the patch, then give me the launch command. Don't quit my running app.

**Build and compaction tests:** macOS, Codex CLI `0.155.0-alpha.9`, desktop bundle `ChatGPT.app`. The installer checks the bundled CLI version, not the app's display version. Other operating systems and versions need a port and verification; the installer will not force an incompatible build.

The repo ships source and a patch, not a prebuilt executable. The first Rust build can take a while and requires substantial free disk space.

## What changes

1. The model finishes a phase and verifies its result.
2. It calls `compact_context` with the objective, useful facts, proof, open work, and the next step.
3. Codex finishes the complete tool batch and runs its existing native compaction.
4. The complete checkpoint is appended to the compacted context, and work resumes in the same turn.

The checkpoint is the **tool-call arguments**, separate from the native compaction summary. No byte limit, list-length limit, or truncation is applied to it. JSON formatting is normalized and omitted optional lists become empty; all supplied string content and list entries survive. Large checkpoints still consume context, so keep useful facts rather than raw logs.

The tool accepts:

```json
{
  "completed_phase": "Found and verified the bug",
  "next_focus": "Implement the fix, then run the regression test",
  "keep": ["Root cause and the relevant file/function", "User's scope and constraints"],
  "open_loops": ["Fix is not implemented yet"],
  "ruled_out": ["The cache is not responsible"],
  "verification": ["The regression test fails for the intended reason"]
}
```

`completed_phase`, `next_focus`, `keep`, and `verification` are required and must contain meaningful, nonempty content. Pending user input cancels the request. Duplicate pending requests are rejected. There is no per-turn compaction count limit: long-running goals can compact at as many verified boundaries as the work needs. Checkpoints are explicitly marked as model-authored working state, never new user authorization.

Normal automatic compaction remains enabled. Token-budget mode keeps its existing `new_context` mechanism and does not expose this tool. Native compaction errors are still errors; this patch removes checkpoint-size rejection, not every possible compaction failure.

## More or less aggressive

We ship **one default: the exact phase-boundary instructions used in the author's daily Codex workflow**, in [AGENTS-snippet.md](AGENTS-snippet.md). There are no untested preset modes.

The model compacts after a verified phase, before materially different work, when the old working context is no longer needed. It keeps unresolved investigation together and carries the objective, constraints, useful facts, commands, proof, and next steps forward.

Install those instructions from the cloned repo:

```sh
python3 manage.py instructions install
```

This adds a marked block to `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`), preserves other content, and saves a backup before changes. Start a new task to load it. Existing unmarked GC instructions cause a clear refusal: ask your agent to merge the included snippet into them rather than add conflicting rules.

**Tune the instructions, without rebuilding.** For example, ask Astra:

> Make my context GC more aggressive: prefer smaller verified phase boundaries, while keeping unresolved debugging work together.

Or:

> Make my context GC less aggressive: compact only at major phase boundaries when substantial old context can be discarded.

These are changes to model judgment, not token thresholds or guaranteed schedules. Edit the managed instruction block directly, or ask Astra to do it. Rerunning `instructions install` restores the shipped wording, so don't use it after customization unless you want that reset. Project instructions can also affect the behavior; see [Codex's AGENTS.md documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Manual installation

Prerequisites: macOS Command Line Tools (`xcode-select --install`), Git, Python 3.9+, Rust via [rustup](https://rustup.rs/), [just](https://github.com/casey/just), and [cargo-nextest](https://nexte.st/docs/installation/). The pinned Codex source selects Rust `1.95.0`. Install missing build dependencies from their official sources; the installer does not install them for you.

```sh
git clone https://github.com/manuelcecchetto/codex-context-gc.git
cd codex-context-gc
python3 manage.py install
python3 manage.py instructions install
```

The installer fetches the pinned upstream tag, verifies its exact commit, checks and applies the patch, runs the targeted compaction tests, builds a separate debug executable, and tests app-server initialization in an isolated temporary Codex home. It also provisions the matching bundled code-mode companion. It does not use credentials or make a model call for the smoke test.

Default installation directory: `~/.local/share/codex-context-gc`. To choose another directory or app location:

```sh
python3 manage.py --prefix "$HOME/.local/share/codex-context-gc-custom" install --app "/Applications/ChatGPT.app"
```

An existing source directory is never overwritten. After a failed build, ask your agent to diagnose the saved checkout, or choose a fresh prefix. Keep using the same `--prefix` for launch.

### Launch

Quit the desktop app normally when convenient, then run from this repo:

```sh
python3 manage.py launch
```

The launcher sets `CODEX_CLI_PATH` for this app process only. It checks both CLI versions, checks the companion executable, and refuses to start while the app is already running. It never terminates your app or modifies the signed bundle, global environment, authentication, or model settings.

**Use this launcher each time you want the patch.** Launching normally from Finder or the Dock uses the stock executable. There is no background updater or persistent app replacement.

### Verify in a fresh task

Ask Astra to complete a small read-only investigation, record a checkpoint, call `compact_context`, then continue with a second step. Check that the tool is available, native compaction happens, the checkpoint appears afterward, and shell tools work both before and after. Installation tests are useful, but this live check establishes that your desktop actually loaded the patch.

### Undo

Quit the app and reopen it normally from Finder or the Dock. To remove this repo's instruction block:

```sh
python3 manage.py instructions off
```

`off` removes the managed guidance; it does not hide the tool in a patched executable or disable native automatic compaction. You can remove the cloned repo and separate installation directory later after preserving anything you want to keep.

## Browser compatibility

The macOS desktop app authenticates local bridge clients using code-signing identities. The bundled Codex executable is OpenAI-signed; a local Rust build has an ad-hoc signature without OpenAI's team identity. Desktop logs show both browser and dynamic app-tools socket rejections with `missing-code-signing-identity`. The in-app browser may be missing from the tool inventory or report "Browser not available" while the normal app browser UI remains usable. Other desktop integrations using the same bridge may also be affected.

This is a compatibility defect of the custom-executable installation approach, not evidence of failed compaction. The installer and app-server smoke test did not exercise these bridges. Matching the CLI version and linking the bundled code-mode host are insufficient. Self-signing cannot reproduce OpenAI's signing identity. This repository does not disable peer authorization or modify the signed app bundle.

Inspect an installation without changing it:

```sh
python3 manage.py doctor
```

For a custom installation prefix, put `--prefix /path/to/install` before `doctor`. The command shows the bundled and custom executable signing metadata; it does not claim live browser compatibility.

**Recovery:** quit the app normally and reopen it from Finder or the Dock, without the custom launcher. This restores the stock executable and removes this patch's `compact_context` tool. Confirm that your browser tools work again. External Chrome remained available in the author's session, but it is not a fix for the in-app browser integration.

There is no verified fix that preserves both this custom executable and the signed desktop bridge. A future solution requires a supported integration with the stock runtime or an upstream change. Do not describe this issue as fixed until the in-app browser and dynamic app tools have been tested with the actual patched installation.

## App updates and compatibility

An app update may change its bundled CLI. The launcher deliberately refuses a mismatch. Ask Astra:

> Update my codex-context-gc installation for the CLI bundled with my current app. Port the patch to the matching upstream source, preserve its compaction and checkpoint guarantees, run the regression tests, and prepare a new separate build. Don't bypass the version check or quit my running app.

See [INSTALL.md](INSTALL.md) for the agent procedure. A newer version is **not** supported merely because the patch applies cleanly.

## Validation and limitations

The published Rust patch passed 18 targeted integration/regression tests, including complete tool-batch preservation, user steering cancellation, same-turn continuation through six compactions, native remote compaction, and checkpoint roundtrips with 70 KB of Unicode and 100 list entries. The debug binary built and isolated app-server initialization passed. The full upstream test suite was not run. A previous version had a successful live desktop compaction; that is not a live validation of every new build or installation.

Checkpoint insertion follows native history replacement; the two are not one transactional write. A crash between them may require recovering notes from the original rollout. Provider context limits still apply. Earlier compaction can discard useful context, and large checkpoints can reduce the benefit. More aggressive is not automatically better; no cost or quality improvement is guaranteed.

The upstream-derived patch and installer are Apache-2.0 licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
