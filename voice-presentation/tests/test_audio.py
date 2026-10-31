# -*- coding: utf-8 -*-
import array
import math
import time
import unittest
from types import SimpleNamespace

import _setup  # noqa: F401
from audio import BLOCK_FRAMES, RATE, SILENCE_WARNING_BLOCKS, AudioInput, Segmenter, rms
from errors import AudioError


def tone_block(amplitude=9000):
    return array.array("h", (int(amplitude * math.sin(i * 0.3)) for i in range(BLOCK_FRAMES))).tobytes()


def silent_block():
    return b"\x00" * (BLOCK_FRAMES * 2)


class RmsTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(rms(silent_block()), 0)
        self.assertGreater(rms(tone_block()), 5000)
        self.assertEqual(rms(b""), 0)


class SegmenterTests(unittest.TestCase):
    def feed_all(self, segmenter, blocks):
        return [s for s in (segmenter.feed(b, i) for i, b in enumerate(blocks)) if s]

    def test_phrase_is_cut_at_pause(self):
        seg = Segmenter()
        blocks = [silent_block()] * 8 + [silent_block()] * 3 + [tone_block()] * 5 + [silent_block()] * 8
        result = self.feed_all(seg, blocks)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].cut)
        self.assertAlmostEqual(result[0].seconds, 1.4, places=1)  # 3 олдинги + 5 овоз + 6 жимлик

    def test_short_noise_is_ignored(self):
        seg = Segmenter()
        blocks = [silent_block()] * 10 + [tone_block()] + [silent_block()] * 10
        self.assertEqual(self.feed_all(seg, blocks), [])

    def test_continuous_speech_is_cut_at_limit(self):
        seg = Segmenter(max_phrase=4.0)
        blocks = [silent_block()] * 8 + [tone_block()] * 100
        result = self.feed_all(seg, blocks)
        self.assertGreaterEqual(len(result), 2)
        self.assertTrue(result[0].cut)
        self.assertAlmostEqual(result[0].seconds, 4.0, places=1)

    def test_adapts_to_background_noise(self):
        seg = Segmenter()
        noisy = tone_block(400)  # шовқин: 300 дан юқори, лекин доимий
        blocks = [noisy] * 10 + [tone_block(9000)] * 4 + [noisy] * 8
        result = self.feed_all(seg, blocks)
        self.assertEqual(len(result), 1)  # шовқиннинг ўзи жумла эмас, лекин овоз топилди


class FakeStream:
    def __init__(self, blocks, fail_after=None):
        self.blocks = list(blocks)
        self.fail_after = fail_after
        self.reads = 0
        self.closed = False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def read(self, frames):
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise OSError("микрофон узилди")
        self.reads += 1
        time.sleep(0.001)
        block = self.blocks[(self.reads - 1) % len(self.blocks)]
        return block, False


class FakeSD:
    def __init__(self, devices, streams, default_input=1):
        self.devices = devices
        self.streams = list(streams)
        self.default = SimpleNamespace(device=(default_input, 0))
        self.opened = []

    def query_devices(self, index=None):
        return self.devices if index is None else self.devices[index]

    def RawInputStream(self, **kwargs):
        self.opened.append(kwargs["device"])
        if not self.streams:
            raise OSError("қурилма банд")
        return self.streams.pop(0)


DEVICES = [
    {"name": "Speakers", "max_input_channels": 0},
    {"name": "Realtek Microphone", "max_input_channels": 2},
    {"name": "USB Headset Mic", "max_input_channels": 1},
]


class AudioInputTests(unittest.TestCase):
    def test_candidates_order(self):
        sd = FakeSD(DEVICES, [])
        self.assertEqual(AudioInput(None, sd).candidates(), [1, 2])
        self.assertEqual(AudioInput("usb", sd).candidates(), [2, 1])
        self.assertEqual(AudioInput(2, sd).candidates(), [2, 1])
        self.assertEqual(AudioInput("yo'q", sd).candidates(), [1, 2])

    def test_no_microphone(self):
        sd = FakeSD([{"name": "Speakers", "max_input_channels": 0}], [])
        with self.assertRaises(AudioError) as ctx:
            AudioInput(None, sd).start()
        self.assertIn("Микрофон топилмади", str(ctx.exception))

    def test_falls_back_to_next_device(self):
        class Flaky(FakeSD):
            def RawInputStream(self, **kwargs):
                self.opened.append(kwargs["device"])
                if kwargs["device"] == 1:
                    raise OSError("банд")
                return self.streams.pop(0)
        sd = Flaky(DEVICES, [FakeStream([tone_block()])])
        audio = AudioInput(None, sd)
        self.assertEqual(audio.start(), "USB Headset Mic")
        self.assertEqual(sd.opened, [1, 2])
        audio.stop()

    def test_all_devices_fail(self):
        sd = FakeSD(DEVICES, [])
        with self.assertRaises(AudioError):
            AudioInput(None, sd).start()

    def test_blocks_are_published_with_timestamps(self):
        sd = FakeSD(DEVICES, [FakeStream([tone_block()])])
        audio = AudioInput(None, sd)
        q = audio.subscribe()
        audio.start()
        stamp, block = q.get(timeout=2)
        audio.stop()
        self.assertEqual(len(block), BLOCK_FRAMES * 2)
        self.assertGreater(stamp, 0)

    def test_reconnects_after_unplug(self):
        sd = FakeSD(DEVICES, [FakeStream([tone_block()], fail_after=3), FakeStream([tone_block()])])
        messages = []
        audio = AudioInput(None, sd, status=messages.append)
        q = audio.subscribe(maxsize=1000)
        audio.start()
        deadline = time.time() + 6
        while len(sd.opened) < 2 and time.time() < deadline:
            time.sleep(0.05)
        count = q.qsize()
        time.sleep(0.3)
        audio.stop()
        self.assertGreaterEqual(len(sd.opened), 2)
        self.assertGreater(q.qsize(), count)  # уланишдан кейин яна овоз келди
        self.assertTrue(any("узилди" in m for m in messages))

    def test_digital_silence_warns_about_privacy_settings(self):
        sd = FakeSD(DEVICES, [FakeStream([silent_block()])])
        messages = []
        audio = AudioInput(None, sd, status=messages.append)
        audio.subscribe(maxsize=10)
        audio.start()
        deadline = time.time() + 6
        while not any("Махфийлик" in m for m in messages) and time.time() < deadline:
            time.sleep(0.05)
        audio.stop()
        self.assertTrue(any("Махфийлик" in m for m in messages))
        self.assertGreaterEqual(SILENCE_WARNING_BLOCKS, 10)

    def test_slow_subscriber_does_not_block(self):
        sd = FakeSD(DEVICES, [FakeStream([tone_block()])])
        audio = AudioInput(None, sd)
        q = audio.subscribe(maxsize=3)
        audio.start()
        time.sleep(0.3)
        audio.stop()
        self.assertLessEqual(q.qsize(), 3)


if __name__ == "__main__":
    unittest.main()
