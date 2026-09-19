#!/usr/bin/env python3
"""Build, launch, and configure the standalone macOS Codex context GC patch."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
START = '<!-- codex-context-gc:start -->'
END = '<!-- codex-context-gc:end -->'


def run(*args, cwd=None):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def output(*args, cwd=None):
    return subprocess.check_output([str(a) for a in args], cwd=cwd, text=True).strip()


def check_version(binary):
    expected = 'codex-cli ' + MANIFEST['version']
    actual = output(binary, '--version')
    if actual != expected:
        raise RuntimeError(f'{binary}: found {actual}; requires {expected}. Ask your agent to port and test the patch against your installed version; do not bypass this guard.')


def configure(path, level):
    """Update only our marked block, retaining a backup when content changes."""
    text = path.read_text() if path.exists() else ''
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise RuntimeError('Malformed or duplicate GC instruction blocks; review the file manually.')
    block = '' if level == 'off' else START + '\n' + (ROOT / 'AGENTS-snippet.md').read_text().rstrip() + '\n' + END
    if START in text:
        first, last = text.index(START), text.index(END)
        if last < first:
            raise RuntimeError('GC instruction markers are out of order.')
        updated = text[:first] + block + text[last + len(END):]
    elif block:
        if 'compact_context' in text:
            raise RuntimeError('Existing unmarked compact_context guidance found. Merge AGENTS-snippet.md into it manually to avoid conflicting instructions.')
        updated = text + ('\n\n' if text else '') + block + '\n'
    else:
        updated = text
    if updated == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        suffix = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copy2(path, path.with_name(path.name + '.gc-backup-' + suffix))
    path.write_text(updated)


def smoke(binary):
    """Test initialization with an isolated home and no model call."""
    with tempfile.TemporaryDirectory(prefix='codex-gc-smoke-') as home:
        env = os.environ.copy()
        env['CODEX_HOME'] = home
        with tempfile.TemporaryFile(mode='w+') as errors:
            process = subprocess.Popen([str(binary), 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, env=env)
            try:
                process.stdin.write(json.dumps({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'context_gc_smoke', 'version': '1.0'}, 'capabilities': {'experimentalApi': True}}}) + '\n')
                process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if not selector.select(timeout=max(0, deadline-time.monotonic())):
                            break
                        line = process.stdout.readline()
                        if not line:
                            raise RuntimeError('App-server exited before initialization.')
                        reply = json.loads(line)
                        if reply.get('id') == 1:
                            if 'error' in reply or 'result' not in reply:
                                raise RuntimeError(f'Initialization failed: {reply}')
                            print('PASS: isolated app-server initialization')
                            return
                    raise RuntimeError('App-server initialization timed out.')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()


def install(prefix, app):
    if platform.system() != 'Darwin':
        raise RuntimeError('The desktop installer currently supports macOS only.')
    for command in ['git', 'cargo', 'rustup', 'just']:
        if shutil.which(command) is None:
            raise RuntimeError(f'Missing {command}; see README prerequisites.')
    # Fail before downloads, builds, or instruction changes on version mismatch.
    check_version(app / 'Contents/Resources/codex')
    output('cargo', 'nextest', '--version')
    source = prefix / 'source'
    if source.exists():
        raise RuntimeError(f'{source} already exists. Preserve it and choose a fresh --prefix for a new build.')
    prefix.mkdir(parents=True, exist_ok=True)
    run('git', 'init', source)
    run('git', 'remote', 'add', 'origin', MANIFEST['upstream'], cwd=source)
    run('git', 'fetch', '--depth=1', 'origin', 'refs/tags/' + MANIFEST['tag'], cwd=source)
    run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=source)
    if output('git', 'rev-parse', 'HEAD', cwd=source) != MANIFEST['commit']:
        raise RuntimeError('Upstream tag no longer matches the pinned commit.')
    run('git', 'apply', '--check', ROOT / 'compact-context.patch', cwd=source)
    run('git', 'apply', ROOT / 'compact-context.patch', cwd=source)
    run('just', 'test', '-p', 'codex-core', '--test', 'all', '-E', 'test(compact_context) | test(remote_compact_v2)', '--retries', '0', cwd=source)
    # Explicit target directory makes the launcher independent of user Cargo settings.
    run('cargo', 'build', '-p', 'codex-cli', '--target-dir', source / 'codex-rs/target', cwd=source / 'codex-rs')
    binary = source / 'codex-rs/target/debug/codex'
    check_version(binary)
    smoke(binary)
    companion = binary.with_name('codex-code-mode-host')
    bundled = app / 'Contents/Resources/codex-code-mode-host'
    if not os.access(bundled, os.X_OK):
        raise RuntimeError('Matching bundled code-mode host is missing or not executable.')
    if not companion.exists() and not companion.is_symlink():
        companion.symlink_to(bundled)
    if not os.access(companion, os.X_OK):
        raise RuntimeError('Existing companion is broken; inspect it without overwriting it.')
    (prefix / 'installation.json').write_text(json.dumps({'app': str(app), 'binary': str(binary), 'version': MANIFEST['version']}, indent=2) + '\n')
    print('Build and initialization passed. Install the GC instructions, then quit the app normally and use the launch command in README.')


def launch(prefix):
    state = json.loads((prefix / 'installation.json').read_text())
    app, binary = Path(state['app']), Path(state['binary'])
    check_version(app / 'Contents/Resources/codex')
    check_version(binary)
    if not os.access(binary.with_name('codex-code-mode-host'), os.X_OK):
        raise RuntimeError('Code-mode companion is missing or broken.')
    executable = app / 'Contents/MacOS/ChatGPT'
    if str(executable) in output('ps', '-axo', 'comm=').splitlines():
        raise RuntimeError('Quit the desktop app normally first. No processes were stopped.')
    env = os.environ.copy()
    env['CODEX_CLI_PATH'] = str(binary)
    os.execve(executable, [str(executable)], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local/share/codex-context-gc')
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('install')
    build.add_argument('--app', type=Path, default=Path('/Applications/ChatGPT.app'))
    sub.add_parser('launch')
    instructions = sub.add_parser('instructions')
    instructions.add_argument('level', choices=['install', 'off'])
    instructions.add_argument('--instructions', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'AGENTS.md')
    args = parser.parse_args()
    if args.command == 'install':
        install(args.prefix.expanduser().resolve(), args.app.expanduser().resolve())
    elif args.command == 'launch':
        launch(args.prefix.expanduser().resolve())
    else:
        configure(args.instructions.expanduser(), args.level)
        print(f'Instructions: {args.level}. Start a new task to load updated instructions.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
