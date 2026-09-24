"""Optional local Qwen raw transcript, with approximate chunk timestamps."""
import os
import wave
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent / 'models' / 'sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25'


def model_available():
    return all((MODEL_DIR / name).is_file() for name in (
        'conv_frontend.onnx', 'encoder.int8.onnx', 'decoder.int8.onnx',
        'tokenizer/vocab.json', 'tokenizer/merges.txt', 'tokenizer/tokenizer_config.json'))


def transcribe_wav(path, language, progress):
    import sherpa_onnx
    if not model_available():
        raise FileNotFoundError(f'本地 Qwen 模型不完整：{MODEL_DIR}；音轨已经保留，不会自动降级或联网下载')
    progress('加载 Qwen3-ASR', 30)
    recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
        conv_frontend=str(MODEL_DIR / 'conv_frontend.onnx'), encoder=str(MODEL_DIR / 'encoder.int8.onnx'),
        decoder=str(MODEL_DIR / 'decoder.int8.onnx'), tokenizer=str(MODEL_DIR / 'tokenizer'),
        num_threads=min(8, os.cpu_count() or 4), provider='cpu', max_total_len=1024, max_new_tokens=512)
    language_name = {'auto': '', 'zh': 'Chinese', 'en': 'English'}[language]
    rows = []
    with wave.open(str(path), 'rb') as source:
        rate = source.getframerate()
        if (rate, source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError('ASR 需要 16 kHz 单声道 16 位 PCM WAV')
        total = source.getnframes()
        position = 0
        while position < total:
            # Up to 28 seconds; choose a low-energy boundary around 25 seconds.
            source.setpos(position)
            probe = np.frombuffer(source.readframes(28 * rate), dtype='<i2').astype(np.float32) / 32768
            if len(probe) == 28 * rate and position + len(probe) < total:
                candidates = range(22 * rate, 28 * rate - 2560, 2560)
                boundary = min(candidates, key=lambda point: float(np.mean(np.abs(probe[point:point + 2560]))))
            else:
                boundary = len(probe)
            start, end = max(0, position - rate), min(total, position + boundary + rate)
            source.setpos(start)
            samples = np.frombuffer(source.readframes(end - start), dtype='<i2').astype(np.float32) / 32768
            if samples.size and np.sqrt(np.mean(np.square(samples), dtype=np.float64)) >= 0.003:
                stream = recognizer.create_stream()
                if language_name:
                    stream.set_option('language', language_name)
                stream.accept_waveform(rate, samples)
                recognizer.decode_stream(stream)
                text = stream.result.text.strip()
                if text:
                    rows.append({'id': len(rows) + 1, 'start': start / rate, 'end': end / rate, 'text': text})
            position += boundary
            progress('Qwen 语音识别', 35 + round(60 * position / max(total, 1)))
    return rows
