import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from media import extract_audio, extract_frames, ffmpeg, probe
from service import create_app


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / 'two-scenes.mp4'
        ffmpeg(['-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=160x120:r=10:d=1',
                '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=10:d=1',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a',
                '-c:v', 'libx264', '-c:a', 'aac', str(cls.video)])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def folder(self):
        folder = Path(tempfile.mkdtemp(dir=self.root))
        return folder

    def test_audio_preserves_original_codec_and_creates_pcm(self):
        folder = self.folder()
        with patch('asr.transcribe_wav', side_effect=AssertionError('ASR must not run')):
            result = extract_audio(self.video, folder, probe(self.video), {'transcribe': False}, lambda *_: None)
        self.assertFalse(result['transcribed'])
        self.assertEqual(probe(folder / 'audio.mka')['streams'][0]['codec'], 'aac')
        with wave.open(str(folder / 'audio.wav')) as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2))
            self.assertGreater(audio.getnframes(), 30000)

    def test_timestamps_and_interval_frames(self):
        for options, expected in [({'mode': 'timestamps', 'timestamps': [1.5, 0, 0]}, [0, 1.5]),
                                  ({'interval': 1}, [0, 1])]:
            folder = self.folder()
            extract_frames(self.video, folder, probe(self.video), options, lambda *_: None)
            frames = json.loads((folder / 'frames.json').read_text())
            self.assertEqual([row['time'] for row in frames], expected)
            self.assertTrue(all((folder / row['path']).is_file() for row in frames))

    def test_scene_detection_keeps_change_and_actual_time(self):
        folder = self.folder()
        result = extract_frames(self.video, folder, probe(self.video), {'mode': 'scene'}, lambda *_: None)
        frames = json.loads((folder / 'frames.json').read_text())
        self.assertEqual(result['count'], 2)
        self.assertAlmostEqual(frames[1]['time'], 1, places=1)

    def test_lossless_scan_crop_and_keyframe_modes(self):
        import struct
        for mode in ['scan', 'keyframes']:
            folder = self.folder()
            result = extract_frames(self.video, folder, probe(self.video), {
                'mode': mode, 'format': 'png', 'crop': [0, 0, 160, 120], 'size': [80, 60],
                'grayscale': True, 'interval': 0.5, 'start': 0, 'end': 2}, lambda *_: None)
            rows = json.loads((folder / 'frames.json').read_text())
            self.assertGreater(result['count'], 0)
            for row in rows:
                data = (folder / row['path']).read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', data[16:24]), (80, 60))
                self.assertEqual(data[25], 0)  # PNG grayscale colour type
            if mode == 'scan':
                self.assertEqual([row['time'] for row in rows], [0, 0.5, 1, 1.5])

    def test_invalid_image_transforms_are_rejected(self):
        for options in [{'format': '../bad'}, {'crop': [0, 0, 999, 120]}, {'size': [0, 60]},
                        {'grayscale': 'yes'}, {'mode': 'scan', 'start': 1, 'end': 0}]:
            with self.assertRaises(ValueError):
                extract_frames(self.video, self.folder(), probe(self.video), options, lambda *_: None)

    def test_invalid_parameters_do_not_silently_drop_requested_frames(self):
        for options in [{'interval': 0}, {'interval': 0.01, 'max_frames': 1},
                        {'mode': 'timestamps', 'timestamps': [-1]}, {'mode': 'timestamps', 'timestamps': [3]},
                        {'mode': 'scene', 'threshold': float('nan')}]:
            with self.assertRaises(ValueError):
                extract_frames(self.video, self.folder(), probe(self.video), options, lambda *_: None)

    def test_missing_audio_is_explicit(self):
        with self.assertRaisesRegex(ValueError, '没有音轨'):
            extract_audio(self.video, self.folder(), {'streams': []}, {}, lambda *_: None)

    def test_api_submit_results_restart_and_local_path_protection(self):
        output = self.folder()
        app = create_app('video', output)
        client = app.test_client()
        self.assertEqual(client.post('/api/jobs', json={'source': 'relative.mp4'}).status_code, 400)
        self.assertEqual(client.post('/api/jobs', json={'source': str(self.video)}, headers={'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(client.post('/api/jobs', json={'source': str(self.video), 'options': {'bad': True}}).status_code, 400)
        response = client.post('/api/jobs', json={'source': str(self.video), 'options': {'interval': 1}})
        self.assertEqual(response.status_code, 202)
        identifier = response.json['id']
        try:
            for _ in range(100):
                state = client.get(f'/api/jobs/{identifier}').json
                if state['status'] in {'done', 'failed'}:
                    break
                time.sleep(0.05)
            self.assertEqual(state['status'], 'done', state)
            other = create_app('video', output)
            self.assertEqual(other.test_client().get(f'/api/jobs/{identifier}').json['status'], 'done')
            other.extensions['worker'].shutdown(wait=True)
            asset = client.get(f'/output/{identifier}/frames/frame-000001.jpg')
            try:
                self.assertEqual(asset.status_code, 200)
            finally:
                asset.close()
            self.assertEqual(client.get(f'/output/{identifier}/../../service.py').status_code, 404)
        finally:
            app.extensions['worker'].shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
