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

    def test_text_is_crisp_by_default(self):
        self.assertEqual(recorder.DEFAULT_QUALITY, 'high')
        high = recorder.QUALITIES['high']
        self.assertEqual((high['frames'], high['max_width'], high['pix_fmt']), ('png', None, 'yuv420p'))
        self.assertLessEqual(high['crf'], 18)
        self.assertEqual(recorder.QUALITIES['max']['pix_fmt'], 'yuv444p')
        self.assertEqual(recorder.QUALITIES['draft']['max_width'], 1280)
        with self.assertRaises(recorder.RecordError):
            recorder.start(Path('/nonexistent'), Path('/tmp/x.mp4'), {}, quality='ultra')

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

    def encode_frames(self, quality, size='640x400', ext='png', ended=None, count=4):
        base = Path(tempfile.mkdtemp(prefix='omadev-rec-'))
        self.addCleanup(shutil.rmtree, base)
        (base / 'frames').mkdir()
        t0 = 100 * S
        for i in range(count):
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', f'testsrc2=s={size}',
                            '-frames:v', '1', str(base / 'frames' / f'{t0 + i * S // 2}.{ext}')], check=True)
        meta = {'file': str(base / 'out.mp4'), 'quality': quality}
        if ended:
            meta['ended'] = t0 + ended
        (base / 'meta.json').write_text(json.dumps(meta))
        out, _, _ = recorder.encode(base)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                'stream=width,height,pix_fmt', '-of', 'csv=p=0', str(out)],
                               capture_output=True, text=True).stdout.strip()
        length = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                 str(out)], capture_output=True, text=True).stdout
        return probe, float(length)  # the file's own length, as a player sees it

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
    def test_qualities_decide_size_and_chroma(self):
        self.assertEqual(self.encode_frames('high', '2000x1000')[0], '2000,1000,yuv420p')  # never downscaled
        self.assertEqual(self.encode_frames('max', '2000x1000')[0], '2000,1000,yuv444p')
        self.assertEqual(self.encode_frames('draft', '2000x1000', 'jpg')[0], '1280,640,yuv420p')
        self.assertEqual(self.encode_frames('draft', '640x400', 'jpg')[0], '640,400,yuv420p')  # never upscaled

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs ffmpeg')
    def test_the_last_frame_lasts_until_the_recorder_stopped(self):
        # repeated frames are dropped, so the last kept frame can be long before the end
        self.assertAlmostEqual(self.encode_frames('high', count=2, ended=6 * S)[1], 6.0, delta=0.1)
        self.assertAlmostEqual(self.encode_frames('high', count=2)[1], 1.0, delta=0.1)  # older recordings

    def test_caption_needs_a_running_recording(self):
        base = Path(tempfile.mkdtemp(prefix='omadev-rec-'))
        self.addCleanup(shutil.rmtree, base)
        with self.assertRaises(recorder.RecordError):
            recorder.caption(base, 'x')


if __name__ == '__main__':
    unittest.main()
