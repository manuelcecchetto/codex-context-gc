#!/usr/bin/env python3
"""Launch the experimental signed-runtime adapter without modifying the app bundle."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys

from manage import check_version, output, smoke

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path('/Applications/ChatGPT.app'))
    parser.add_argument('--check', action='store_true', help='Check signature/version and initialization; do not launch the desktop')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('The signed desktop launcher currently supports macOS only.')
    app = args.app.expanduser().resolve()
    stock = app / 'Contents/Resources/codex'
    check_version(stock)
    subprocess.run(['codesign', '--verify', '--strict', str(stock)], check=True)
    signature = subprocess.run(['codesign', '-d', '--verbose=4', str(stock)], check=True,
                               capture_output=True, text=True)
    if 'TeamIdentifier=2DC432GLL2' not in signature.stderr.splitlines():
        raise RuntimeError('Expected the original OpenAI-signed bundled runtime.')
    adapter = ROOT / 'stock_gc.py'
    if not os.access(adapter, os.X_OK):
        raise RuntimeError('stock_gc.py is not executable; restore its executable file mode.')
    if args.check:
        os.environ['CODEX_GC_STOCK_BINARY'] = str(stock)
        smoke(adapter)
        print('PASS: pinned version and OpenAI signature. Live desktop browser verification still required.')
        return
    executable = app / 'Contents/MacOS/ChatGPT'
    if str(executable) in output('ps', '-axo', 'comm=').splitlines():
        raise RuntimeError('Quit the desktop app normally first. No processes were stopped.')
    env = os.environ.copy()
    env['CODEX_CLI_PATH'] = str(adapter)
    env['CODEX_GC_STOCK_BINARY'] = str(stock)
    os.execve(executable, [str(executable)], env)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
