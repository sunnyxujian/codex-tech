"""Skill-facing CLI. Progress goes to stderr, final manifest JSON to stdout."""
import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from media import ROOT, probe, write_json

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(port, path, data=None):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body,
                                 headers={'Content-Type': 'application/json'} if body else {})
    with HTTP.open(req, timeout=10) as response:
        return json.load(response)


def extract(args):
    source = args.source.resolve(strict=True)
    info = probe(source)
    kinds = {stream['type'] for stream in info['streams']}
    selection = ['audio'] if args.audio_only else ['video'] if args.frames_only else [kind for kind in ['audio', 'video'] if kind in kinds]
    if not selection:
        raise ValueError('文件没有音频或视频流')
    submitted = {}
    bundle = ROOT / 'output' / 'bundles' / uuid.uuid4().hex
    bundle.mkdir(parents=True)
    manifest = {'source': str(source), 'metadata': info, 'status': 'running', 'services': submitted,
                'manifest': str(bundle / 'manifest.json')}
    for kind in selection:
        port = {'audio': 8765, 'video': 8766}[kind]
        health = request(port, '/health')
        if not health.get('ok') or health.get('service') != kind or Path(health.get('project_root', '')).resolve() != ROOT:
            raise RuntimeError(f'{port} 不是 video-mate-pick 的服务，请检查 services.ps1 status')
        options = {'transcribe': not args.no_transcript, 'language': args.language} if kind == 'audio' else {
            'mode': 'timestamps' if args.timestamps else args.mode, 'interval': args.interval,
            'threshold': args.threshold, 'max_frames': args.max_frames}
        if kind == 'video' and args.timestamps:
            options['timestamps'] = args.timestamps
        job = request(port, '/api/jobs', {'source': str(source), 'options': options})
        submitted[kind] = {'port': port, **job}
        write_json(bundle / 'manifest.json', manifest)
    deadline = time.monotonic() + args.timeout
    previous = {}
    while time.monotonic() < deadline:
        done = True
        failed = []
        for kind, job in submitted.items():
            state = request(job['port'], f'/api/jobs/{job["id"]}')
            job.update(state)
            if state['message'] != previous.get(kind):
                print(f'{kind}: {state["message"]}', file=sys.stderr, flush=True)
                previous[kind] = state['message']
            done &= state['status'] in {'done', 'failed'}
            if state['status'] == 'failed':
                failed.append(kind)
        manifest['status'] = 'failed' if done and failed else 'done' if done else 'running'
        write_json(bundle / 'manifest.json', manifest)
        if done:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 1 if failed else 0
        time.sleep(1)
    raise TimeoutError(f'等待超时，服务任务仍继续运行，任务编号保存在 {bundle / "manifest.json"}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='提取视频原音轨、可选逐字稿和画面，供 Skill 读取')
    parser.add_argument('source', type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--audio-only', action='store_true')
    group.add_argument('--frames-only', action='store_true')
    parser.add_argument('--no-transcript', action='store_true')
    parser.add_argument('--language', choices=['auto', 'zh', 'en'], default='auto')
    parser.add_argument('--mode', choices=['interval', 'scene'], default='interval')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--timestamps', type=float, nargs='+')
    parser.add_argument('--threshold', type=float, default=0.25)
    parser.add_argument('--max-frames', type=int, default=2000)
    parser.add_argument('--timeout', type=float, default=86400)
    args = parser.parse_args()
    try:
        sys.exit(extract(args))
    except Exception as error:
        parser.exit(1, f'提取失败：{error}\n')
