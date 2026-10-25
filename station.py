"""Small, standalone ADB transport. No voice assistant dependencies."""
import hashlib
from pathlib import Path
import shlex
import subprocess

class Station:
    def __init__(self, serial):
        if not serial or serial.startswith('-'):
            raise ValueError('Specify a station ADB serial explicitly')
        self.serial = serial
        self.adb = ['adb', '-s', serial]

    def connect(self):
        if ':' in self.serial:
            subprocess.run(['adb', 'connect', self.serial], check=True, timeout=10)
        self.command('get-state')
        if self.command('shell', 'id -u').stdout.strip() != b'0':
            raise RuntimeError('Root ADB is required; this project does not obtain root')

    def command(self, *args, timeout=20):
        shell = args and args[0] == 'shell'
        argv = list(args)
        if shell:
            # Old adbd does not propagate the remote shell exit code.
            argv = ['shell', argv[1].rstrip() + '\n__mc_rc=$?; printf "\\n__MC_RC=%s\\n" "$__mc_rc"']
        result = subprocess.run(self.adb + argv, check=True, capture_output=True, timeout=timeout)
        if shell:
            body, marker, code = result.stdout.replace(b'\r', b'').rpartition(b'__MC_RC=')
            if not marker or code.strip() != b'0':
                raise RuntimeError('Station command failed: ' + body.decode(errors='replace')[-3000:])
            result.stdout = body.rstrip(b'\n')
        return result

    def deploy(self, source, destination, mode='644'):
        source = Path(source)
        target = shlex.quote(destination)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        current = self.command('shell', f'if [ -f {target} ]; then sha256sum {target}; fi').stdout.split()
        if current and current[0].decode() == digest:
            return
        self.command('shell', 'mkdir -p ' + shlex.quote(str(Path(destination).parent)))
        self.command('push', str(source), destination + '.new', timeout=120)
        stage = shlex.quote(destination + '.new')
        self.command('shell', f'chmod {mode} {stage} && mv {stage} {target}')
