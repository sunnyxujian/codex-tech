"""Detach a service without inheriting the caller's console or pipe handles."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
name = sys.argv[1]
entry = ROOT / {'audio': 'audio_service.py', 'video': 'frame_service.py'}[name]
logs = ROOT / '.runtime'
logs.mkdir(exist_ok=True)
with (logs / f'{name}.stdout.log').open('ab') as output, (logs / f'{name}.stderr.log').open('ab') as error:
    process = subprocess.Popen([sys.executable, '-X', 'utf8', str(entry)], cwd=entry.parent,
                               stdin=subprocess.DEVNULL, stdout=output, stderr=error, close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
print(process.pid)
