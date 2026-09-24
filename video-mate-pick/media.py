"""Raw audio and frame extraction; no editorial or model-generated content."""
import json
import math
import re
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def probe(source):
    with av.open(str(source)) as container:
        streams = []
        for stream in container.streams:
            item = {'index': stream.index, 'type': stream.type,
                    'codec': stream.codec_context.name if stream.codec_context else None,
                    'time_base': str(stream.time_base),
                    'start_time': float(stream.start_time * stream.time_base) if stream.start_time is not None else None,
                    'duration': float(stream.duration * stream.time_base) if stream.duration is not None else None}
            if stream.type == 'video':
                item.update(width=stream.width, height=stream.height,
                            fps=float(stream.average_rate) if stream.average_rate else None)
            if stream.type == 'audio':
                item.update(sample_rate=stream.codec_context.sample_rate, channels=stream.codec_context.channels)
            streams.append(item)
        duration = container.duration / av.time_base if container.duration is not None else max(
            (item['duration'] or 0 for item in streams), default=0)
        return {'source': str(source.resolve()), 'bytes': source.stat().st_size,
                'start_time': container.start_time / av.time_base if container.start_time is not None else None,
                'duration': duration, 'streams': streams}


def ffmpeg(arguments):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-nostdin', '-y', *arguments]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('FFmpeg 处理失败：' + result.stderr[-2000:])
    return result


def extract_audio(source, folder, info, options, progress):
    tracks = [stream for stream in info['streams'] if stream['type'] == 'audio']
    if not tracks:
        raise ValueError('文件没有音轨')
    index = options.get('stream_index', tracks[0]['index'])
    if type(index) is not int or index not in [track['index'] for track in tracks]:
        raise ValueError('stream_index 必须是 metadata 中的音轨索引')
    transcribe = options.get('transcribe', True)
    if not isinstance(transcribe, bool):
        raise ValueError('transcribe 必须为布尔值')
    language = options.get('language', 'auto')
    if language not in {'auto', 'zh', 'en'}:
        raise ValueError('language 必须是 auto、zh 或 en')
    progress('提取原始音轨', 10)
    ffmpeg(['-loglevel', 'error', '-i', str(source), '-map', f'0:{index}', '-vn', '-c:a', 'copy', str(folder / 'audio.mka')])
    progress('生成 16 kHz 单声道 WAV', 25)
    ffmpeg(['-loglevel', 'error', '-i', str(source), '-map', f'0:{index}', '-vn',
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(folder / 'audio.wav')])
    result = {'audio': 'audio.mka', 'wav': 'audio.wav', 'stream_index': index,
              'source_stream_start_time': next(track['start_time'] for track in tracks if track['index'] == index),
              'transcribed': False, 'timestamp_basis': 'seconds from extracted track start'}
    if transcribe:
        from asr import transcribe_wav
        rows = transcribe_wav(folder / 'audio.wav', language, progress)
        write_json(folder / 'transcript.json', rows)
        (folder / 'transcript.txt').write_text('\n'.join(row['text'] for row in rows) + ('\n' if rows else ''), encoding='utf-8')
        result.update(transcribed=True, transcript='transcript.txt', segments='transcript.json',
                      model='qwen3-asr-0.6b-int8', segment_count=len(rows),
                      alignment='approximate chunk boundaries with overlapping context; not word-level alignment')
    return result


def extract_frames(source, folder, info, options, progress):
    if not any(item['type'] == 'video' for item in info['streams']):
        raise ValueError('文件没有视频画面')
    mode = options.get('mode', 'interval')
    limit = options.get('max_frames', 2000)
    if type(limit) is not int or not 1 <= limit <= 20000:
        raise ValueError('max_frames 必须是 1 到 20000 的整数')
    duration = info['duration']
    extension = options.get('format', 'jpg')
    if extension not in {'jpg', 'png'}:
        raise ValueError('format 必须是 jpg 或 png')
    filters = []
    crop = options.get('crop')
    if crop is not None:
        video = next(item for item in info['streams'] if item['type'] == 'video')
        if (not isinstance(crop, list) or len(crop) != 4 or any(type(v) is not int for v in crop)
                or min(crop[:2]) < 0 or min(crop[2:]) < 1
                or crop[0] + crop[2] > video['width'] or crop[1] + crop[3] > video['height']):
            raise ValueError('crop 必须为画面范围内的 [x,y,width,height] 整数列表')
        filters.append(f'crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}')
    size = options.get('size')
    if size is not None:
        if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 1 <= v <= 8192 for v in size):
            raise ValueError('size 必须为 1 到 8192 范围内的 [width,height] 整数列表')
        filters.append(f'scale={size[0]}:{size[1]}')
    if not isinstance(options.get('grayscale', False), bool):
        raise ValueError('grayscale 必须是布尔值')
    if options.get('grayscale'):
        filters.append('format=gray')
    encode = ['-q:v', '2'] if extension == 'jpg' else []
    if mode == 'timestamps':
        times = options.get('timestamps')
        if not isinstance(times, list) or not times or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 or t >= duration for t in times):
            raise ValueError('timestamps 必须为视频时长范围内的非空秒数列表')
        times = sorted(set(times))
    elif mode == 'interval':
        interval = options.get('interval', 5)
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('interval 必须是大于零的有限秒数')
        count = math.ceil(duration / interval)
        if count > limit:
            raise ValueError(f'预计 {count} 张截图，超过 max_frames={limit}；请增大间隔或显式提高上限')
        times = [i * interval for i in range(count)]
    elif mode == 'scene':
        threshold = options.get('threshold', 0.25)
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 1:
            raise ValueError('threshold 必须在 0 到 1 之间')
        times = None
    elif mode in {'keyframes', 'scan'}:
        times = None
        if mode == 'scan':
            interval = options.get('interval', 2)
            start = options.get('start', 0)
            end = options.get('end', duration)
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (interval, start, end))
                    or interval <= 0 or not 0 <= start < end <= duration):
                raise ValueError('scan 需要有效的 interval、start 和 end')
            if math.ceil((end - start) / interval) > limit:
                raise ValueError('扫描截图数量超过 max_frames')
    else:
        raise ValueError('mode 必须是 interval、timestamps、scene、keyframes 或 scan')
    if times is not None and len(times) > limit:
        raise ValueError('截图数量超过 max_frames；未截断请求，请调整参数')
    frames_dir = folder / 'frames'
    frames_dir.mkdir()
    rows = []
    if times is None:
        progress('扫描视频画面', 20)
        before_input = []
        if mode == 'scene':
            selection = ['setpts=PTS-STARTPTS', f"select='eq(n,0)+gt(scene,{threshold})'"]
        elif mode == 'keyframes':
            before_input = ['-skip_frame', 'nokey']
            selection = ['setpts=PTS-STARTPTS']
        else:
            before_input = ['-ss', str(start), '-t', str(end - start)]
            selection = [f'fps={1.0 / interval:.9f}']
        result = ffmpeg([*before_input, '-i', str(source), '-map', '0:v:0', '-an', '-vf',
                         ','.join([*selection, *filters, 'showinfo']),
                         '-vsync', 'vfr', '-frames:v', str(limit + 1), *encode,
                         str(frames_dir / f'frame-%06d.{extension}')])
        selected = [float(value) for value in re.findall(r'\bn:\s*\d+.*?\bpts_time:([\d.eE+-]+)', result.stderr)]
        files = sorted(frames_dir.glob(f'frame-*.{extension}'))
        if len(files) > limit:
            raise ValueError('场景截图数量超过 max_frames；请提高上限或 threshold，任务目录保留已提取画面')
        if len(files) != len(selected):
            raise RuntimeError('截图与时间索引数量不一致')
        if mode == 'scan':
            selected = [start + index * interval for index in range(len(files))]
        rows = [{'path': 'frames/' + file.name, 'time': second} for file, second in zip(files, selected)]
    else:
        for index, second in enumerate(times, 1):
            target = frames_dir / f'frame-{index:06d}.{extension}'
            transforms = ['-vf', ','.join(filters)] if filters else []
            result = ffmpeg(['-ss', str(second), '-i', str(source), '-map', '0:v:0', '-an',
                            *transforms, '-frames:v', '1', *encode, str(target)])
            if not target.is_file() or not target.stat().st_size:
                raise RuntimeError(f'无法提取 {second} 秒处的画面')
            rows.append({'path': 'frames/' + target.name, 'time': second})
            progress(f'提取画面 {index}/{len(times)}', 10 + round(85 * index / len(times)))
    if not rows:
        raise ValueError('没有提取到画面')
    write_json(folder / 'frames.json', rows)
    return {'frames': 'frames.json', 'count': len(rows), 'mode': mode,
            'timestamp_basis': 'video playback seconds',
            'time_kind': 'decoded frame time' if mode in {'scene', 'keyframes'} else 'requested sample time (nearest available frame)'}
