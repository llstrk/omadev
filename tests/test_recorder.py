"""recorder.py: caption timing, frame scale and a real encode of synthetic frames."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
import recorder  # noqa: E402

S = 1_000_000_000


class RecorderTests(unittest.TestCase):
    def test_captions_last_until_the_next_and_empty_clears(self):
        t0 = 10 * S
        caps = [(t0 - S, 'early'), (t0 + 2 * S, 'two'), (t0 + 5 * S, ''), (t0 + 7 * S, 'seven')]
        self.assertEqual(recorder.caption_spans(caps, t0, 9.0),
                         [(0.0, 2.0, 'early'), (2.0, 5.0, 'two'), (7.0, 9.0, 'seven')])

    def test_frames_are_scaled_to_about_full_hd(self):
        self.assertEqual(recorder.frame_scale(1520), 1.0)
        self.assertEqual(recorder.frame_scale(3052), 0.629)
        self.assertEqual(recorder.frame_scale(1520, 2.0), 2.0)  # grim scales the 760-wide layout
        self.assertEqual(recorder.frame_scale(3052, 2.0), 1.258)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe') and shutil.which('fc-match'),
                         'needs ffmpeg and fontconfig')
    def test_encode_keeps_real_time_and_makes_a_sheet(self):
        base = Path(tempfile.mkdtemp(prefix='omadev-rec-'))
        self.addCleanup(shutil.rmtree, base)
        frames = base / 'frames'
        frames.mkdir()
        t0 = 100 * S
        for i in range(12):  # 12 frames, 0.25 s apart: 3 s of video
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', f'color=c=0x{i * 20:02x}2040:s=320x200',
                            '-frames:v', '1', str(frames / f'{t0 + i * S // 4}.jpg')], check=True)
        (frames / f'{t0 + 5 * S}.part').write_text('cut off')  # an unfinished frame is ignored
        (base / 'meta.json').write_text(json.dumps({'file': str(base / 'out.mp4')}))
        (base / 'captions.jsonl').write_text(json.dumps({'t': t0 + S, 'text': "it's 50% done: ok"}) + '\n')
        out, seconds, sheet = recorder.encode(base, sheet=True)
        self.assertAlmostEqual(seconds, 3.0, places=2)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                str(out)], capture_output=True, text=True).stdout
        self.assertAlmostEqual(float(probe), 3.0, delta=0.3)
        self.assertTrue(sheet.is_file())

    def test_caption_needs_a_running_recording(self):
        base = Path(tempfile.mkdtemp(prefix='omadev-rec-'))
        self.addCleanup(shutil.rmtree, base)
        with self.assertRaises(recorder.RecordError):
            recorder.caption(base, 'x')


if __name__ == '__main__':
    unittest.main()
