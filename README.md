# Codex Context GC

Expose `compact_context` so the model can compact after a verified phase, before carrying old tool results into the next phase. Codex runs its native compaction, the complete model-authored checkpoint is appended, and work continues automatically.

The default desktop setup now uses the **original OpenAI-signed runtime** with a small Python adapter. No Rust build or app-bundle modification is required. Native compaction, checkpoint preservation, automatic continuation, and shell plus in-app browser access after compaction passed a live desktop test.

Unofficial; not an OpenAI product. Inspired by [pi-context-gc](https://github.com/manuelcecchetto/pi-context-gc).

## Install: ask Astra

> Install https://github.com/manuelcecchetto/codex-context-gc for my local Codex desktop app. Follow README.md and INSTALL.md, use the signed-runtime adapter, check compatibility, and install the included compaction instructions while preserving my existing setup. Test it and give me the launch command. Don't quit my running app.

Tested on macOS with the `ChatGPT.app` bundle and bundled CLI `0.155.0-alpha.9`. The launcher checks the exact CLI version and OpenAI signing identity. Other versions and platforms need separate verification.

## Manual setup

Requires Git and Python 3.9+; keep the cloned repository in place.

```sh
git clone https://github.com/manuelcecchetto/codex-context-gc.git
cd codex-context-gc
python3 stock_gc_launch.py --check
python3 manage.py instructions install
```

The instruction command preserves unrelated content in `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`) and backs it up before changes. If you already have unmarked GC instructions, ask your agent to merge [AGENTS-snippet.md](AGENTS-snippet.md) into them.

Quit the app normally, then launch from the repository:

```sh
python3 stock_gc_launch.py
```

For a different app location, pass `--app /path/to/ChatGPT.app`. Start a **new task**: tasks created before installing the adapter do not automatically acquire the tool. Use this launcher on subsequent starts. It refuses to terminate an already running app and changes only the environment of the app it launches.

Ask the new task to test `compact_context`, then verify shell and Codex in-app browser access after automatic continuation.

### Existing Rust-patch users

Update the repository to `main`, run the check above, quit normally, and use `stock_gc_launch.py` instead of the previous launcher. Keep your existing GC instructions. No rebuild is needed; you may retain the old build for rollback. `manage.py install`, `manage.py launch`, and `manage.py doctor` are **legacy Rust-build commands**, not the default desktop setup.

## How it works

1. The model finishes and verifies a phase.
2. It calls `compact_context` alone with useful working state, then ends the phase turn.
3. Native lifecycle hooks coordinate compaction and insert the complete checkpoint before continuation.
4. Work continues in a **new turn within the same task**. Active goals use Codex's own continuation scheduler.

The checkpoint is the tool-call arguments, separate from the native summary:

```json
{
  "completed_phase": "Located and verified the bug",
  "next_focus": "Implement the fix and run the regression test",
  "keep": ["Objective, scope, constraints, relevant paths and findings"],
  "verification": ["Regression test fails for the intended reason"],
  "open_loops": ["Fix remains to be implemented"],
  "ruled_out": ["Cache invalidation is not responsible"]
}
```

The first four fields are required. There is no checkpoint byte cap, list-length cap, truncation, or compaction-count cap. JSON formatting is normalized and omitted optional lists become empty; supplied string values and list entries are preserved. Checkpoints are marked as model-authored state, not new user authorization. New user input cancels pending automatic continuation. Normal native automatic compaction remains enabled.

## More or less aggressive

[AGENTS-snippet.md](AGENTS-snippet.md) contains the phase-boundary instructions used in the author's workflow. There are no preset modes. Ask Astra to edit your instructions to prefer smaller verified phases for more frequent compaction, or major boundaries with substantial obsolete context for less frequent compaction. Keep unresolved investigations together.

These are model instructions, not token thresholds or guaranteed schedules. Rerunning `instructions install` restores the shipped wording, so avoid that after customization unless you want to reset it.

## Browser compatibility

The original custom Rust binary caused `missing-code-signing-identity` failures in the desktop browser and app-tools bridges. The signed-runtime adapter preserves the original signed CLI and its desktop parent. It does not disable peer authorization, re-sign binaries, or modify the app bundle.

Live verification on 2026-09-19 passed native compaction, automatic continuation, complete checkpoint preservation, and post-compaction shell and in-app browser access. A separate app-tools read also passed. See [SIGNED-RUNTIME.md](SIGNED-RUNTIME.md) for the implementation and validation details. The old build is retained under [LEGACY-RUST.md](LEGACY-RUST.md).

## Validation and limitations

The 21-test suite includes real bundled-runtime tests with a localhost fixture provider: ordinary continuation, six compactions in an active goal, goal completion, and the desktop's configuration-argument ordering. These tests use an isolated home and make no paid model calls.

```sh
CODEX_GC_TEST_STOCK=/Applications/ChatGPT.app/Contents/Resources/codex \
  python3 -m unittest discover -s tests -v
```

This remains experimental. Live desktop proof covers one end-to-end handoff, not every failure case or workflow. Crash recovery, forked-task inheritance, and broader failure recovery need further validation. A finished phase may display as interrupted during handoff. Checkpoints persist with owner-only permissions in `$CODEX_HOME/context-gc-checkpoints/` and may contain private task state. Native compaction and checkpoint insertion are not one atomic operation.

Provider context limits still apply. Earlier compaction can discard useful information; large checkpoints consume context. Cost and quality gains are not guaranteed, and measurements from the original Rust patch are not benchmarks of this adapter.

An app update may change the bundled CLI. Do not bypass the version guard: ask your agent to adapt and test the adapter against the new protocol and hooks before changing the pinned version.

## Undo

Quit and reopen the app normally from Finder or the Dock. To remove the managed instruction block:

```sh
python3 manage.py instructions off
```

This preserves your tasks, rollouts, and unrelated configuration. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for licensing.
