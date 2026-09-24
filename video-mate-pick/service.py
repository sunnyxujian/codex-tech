"""Local-file job API shared by audio and frame services."""
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from media import ROOT, extract_audio, extract_frames, probe, write_json


def create_app(kind, output_root=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    output_root = Path(output_root) if output_root else ROOT / 'output' / kind
    jobs = {}
    lock = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix=kind)
    app.extensions['worker'] = worker

    def run(job_id, source, folder, options):
        def progress(message, percent):
            with lock:
                jobs[job_id].update(status='running', message=message, percent=percent)
        try:
            progress('读取媒体信息', 1)
            metadata = probe(source)
            write_json(folder / 'metadata.json', metadata)
            result = (extract_audio if kind == 'audio' else extract_frames)(source, folder, metadata, options, progress)
            result.update(metadata='metadata.json', source=str(source), output=str(folder), service=kind)
            write_json(folder / 'result.json', result)
            with lock:
                jobs[job_id].update(status='done', message='完成', percent=100, result=result)
        except Exception as error:
            write_json(folder / 'error.json', {'error': str(error)})
            with lock:
                jobs[job_id].update(status='failed', message=str(error))

    @app.before_request
    def local_only():
        origin = request.headers.get('Origin')
        if origin:
            # These APIs read local paths; do not allow browser-origin requests.
            abort(403)
        if request.host.split(':')[0] not in {'127.0.0.1', 'localhost'}:
            abort(403)

    @app.get('/health')
    def health():
        from asr import model_available
        return jsonify(ok=True, service=kind, project_root=str(ROOT), pid=os.getpid(), python=os.sys.executable,
                       output=str(output_root), model='qwen3-asr-0.6b-int8' if kind == 'audio' else 'ffmpeg',
                       asr_available=model_available() if kind == 'audio' else None)

    @app.get('/')
    def index():
        return jsonify(service=kind, description='本地视频原始素材提取 API',
                       endpoints=['GET /health', 'POST /api/jobs', 'GET /api/jobs/<id>', 'GET /output/<id>/<filename>'])

    @app.post('/api/jobs')
    def submit():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('source'), str):
            return jsonify(error='请用 JSON 提供本机文件的绝对路径 source'), 400
        source = Path(data['source'])
        if not source.is_absolute() or not source.is_file():
            return jsonify(error='source 必须是存在的本机文件绝对路径'), 400
        options = data.get('options', {})
        allowed = {'transcribe', 'language', 'stream_index'} if kind == 'audio' else {
            'mode', 'interval', 'timestamps', 'threshold', 'max_frames',
            'format', 'crop', 'size', 'grayscale', 'start', 'end'}
        if not isinstance(options, dict) or set(options) - allowed:
            return jsonify(error='options 包含不支持的参数'), 400
        job_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        folder = output_root / job_id
        folder.mkdir(parents=True)
        write_json(folder / 'request.json', {'source': str(source.resolve()), 'options': options})
        with lock:
            jobs[job_id] = {'id': job_id, 'status': 'queued', 'percent': 0, 'message': '排队等待', 'output': str(folder)}
        worker.submit(run, job_id, source.resolve(), folder, options)
        return jsonify(id=job_id, output=str(folder), status_url=f'/api/jobs/{job_id}'), 202

    @app.get('/api/jobs/<job_id>')
    def status(job_id):
        if not valid_id(job_id):
            abort(404)
        with lock:
            if job_id in jobs:
                return jsonify(jobs[job_id])
        folder = output_root / job_id
        if (folder / 'result.json').is_file():
            return jsonify(id=job_id, status='done', percent=100, message='完成', output=str(folder),
                           result=json.loads((folder / 'result.json').read_text(encoding='utf-8')))
        if (folder / 'request.json').is_file():
            error = folder / 'error.json'
            return jsonify(id=job_id, status='failed', output=str(folder), percent=0,
                           message=json.loads(error.read_text(encoding='utf-8'))['error'] if error.exists()
                           else '服务重启，任务已中断；已提取素材保留，请重新提交')
        abort(404)

    @app.get('/output/<job_id>/<path:filename>')
    def output(job_id, filename):
        if not valid_id(job_id):
            abort(404)
        return send_from_directory(output_root / job_id, filename)

    return app


def valid_id(value):
    return bool(value) and all(c.isascii() and (c.isalnum() or c == '-') for c in value)
