# Signed-runtime adapter (experimental)

The original Rust patch replaces the app's signed CLI with a locally built CLI. On macOS, the in-app browser and app-tools bridges can reject that process with `missing-code-signing-identity`.

This candidate keeps the original OpenAI-signed CLI. A small local Python adapter adds the GC tool through the app-server protocol. The desktop-launched process executes the original binary at the same PID, retaining its desktop parent; a child handles stdio forwarding. The app bundle and native peer authorization remain unchanged.

**Status:** local integration tests pass against the pinned bundled runtime, including six compactions within an active goal. Live desktop browser and app-tools reads passed after relaunch. The first desktop run did not receive the GC tool: a subcommand configuration flag discarded the root-level hook overrides. Coordinator flags now follow the desktop arguments in the app-server scope. The exact desktop-argument regression passes; live GC verification after relaunch remains pending. Redacted startup/registration counters are saved as `adapter-<pid>.status` in the checkpoint directory for diagnosis. This is a candidate repair, not a verified desktop release.

## Try the candidate

Requires macOS, Python 3, and the exact app CLI version in `manifest.json`. No Rust build is needed.

```sh
python3 stock_gc_launch.py --check
```

Quit the app normally, then run this from the repository:

```sh
python3 stock_gc_launch.py
```

Start a **new task** to get the dynamic tool. Existing tasks created by the Rust patch do not automatically gain it. Keep the existing `AGENTS-snippet.md` instructions. On subsequent launches use this launcher again. Rollback: quit and launch the app normally. No global environment variables or app files are changed.

After relaunch, verify in-app browser discovery and a page read, one actual `compact_context` handoff, shell execution after the handoff, and any app connectors you use. A passing signature check alone does not prove live bridge compatibility.

## What changes

The model supplies the same checkpoint fields. The adapter saves the full checkpoint, asks the model to end its phase turn, and uses native Stop/PostCompact hooks to coordinate native compaction and checkpoint insertion. The PostCompact hook waits until the checkpoint has been persisted before allowing continuation. Active goals use Codex's own scheduler; ordinary tasks receive an automatic continuation turn.

Unlike the Rust patch, continuation is a **new turn in the same task**. The finished phase may display as interrupted when the Stop hook hands over to the native compaction task. There is no checkpoint length, list-length, or request-count cap. Values are preserved verbatim in JSON, with explicit attribution as model-authored state rather than new user authorization. Normal automatic compaction remains native.

Only the adapter's two exact command hashes are enabled through per-task session configuration. Other hooks and tool requests retain their existing handlers. Missing coordinator hooks disable the new GC tool. New user input cancels pending automatic continuation. Checkpoints are written with owner-only permissions under `$CODEX_HOME/context-gc-checkpoints/` (default `~/.codex/context-gc-checkpoints/`); they may contain private task state and persist until you remove them.

This prototype does not recover an in-flight handoff automatically after a process crash. API/hook failures retain the saved checkpoint and emit diagnostics; failure recovery, forked-task inheritance, and the live desktop bridges require further validation before making this the default installer.

## Verification

```sh
python3 -m unittest discover -s tests -v
CODEX_GC_TEST_STOCK=/Applications/ChatGPT.app/Contents/Resources/codex \
  python3 -m unittest discover -s tests -p test_stock_runtime.py -v
```

The opt-in tests execute the real bundled CLI with an isolated home and a localhost fixture provider. They assert the actual process image, native compaction summary, full Unicode checkpoint in the next inference, ordinary continuation, six goal compactions, and goal completion. They make no paid model calls and do not use your normal credentials or tasks.
