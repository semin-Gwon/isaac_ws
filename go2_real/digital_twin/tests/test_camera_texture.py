"""Exercise the camera's native-upload inputs without launching Isaac Sim."""

import ast
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np


# go2_visualize starts Isaac Sim at import time. Load its actual screen class
# without executing the launcher; only the native provider is replaced here.
source = Path(__file__).resolve().parents[1] / "go2_visualize.py"
screen_definition = next(node for node in ast.parse(source.read_text()).body
                         if isinstance(node, ast.ClassDef) and node.name == "VirtualCameraScreen")
namespace = dict(np=np, time=time)
exec(compile(ast.Module(body=[screen_definition], type_ignores=[]), str(source), "exec"), namespace)
VirtualCameraScreen = namespace["VirtualCameraScreen"]


class CameraTextureTests(unittest.TestCase):
    def setUp(self):
        self.uploads = []
        self.screen = VirtualCameraScreen.__new__(VirtualCameraScreen)
        self.screen.texture_provider = SimpleNamespace(
            set_data_array=lambda data, size: self.uploads.append((data, size)))
        self.screen._last_texture_update = float("-inf")
        self.screen._update_period_sec = 0.1
        self.screen.texture_update_count = 0

    def test_strided_rgb_becomes_tightly_packed_rgba(self):
        rgb = np.arange(72, dtype=np.uint8).reshape(4, 6, 3)[::2, ::2, ::-1]
        self.assertFalse(rgb.flags.c_contiguous)
        self.screen.update_texture(rgb)
        rgba, size = self.uploads[0]
        self.assertEqual(size, [3, 2])
        self.assertTrue(rgba.flags.c_contiguous)
        np.testing.assert_array_equal(rgba[:, :, :3], rgb)
        np.testing.assert_array_equal(rgba[:, :, 3], np.full((2, 3), 255))

    def test_rate_limit_and_previous_upload_buffer_stays_unchanged(self):
        rgb = np.full((2, 3, 3), 10, dtype=np.uint8)
        with patch.object(time, "monotonic", side_effect=[1.0, 1.05, 1.11]):
            self.screen.update_texture(rgb)
            rgb[:] = 200
            self.screen.update_texture(rgb)
            self.screen.update_texture(rgb)
        self.assertEqual(self.screen.texture_update_count, 2)
        self.assertEqual(len(self.uploads), 2)
        np.testing.assert_array_equal(self.uploads[0][0][:, :, :3], np.full((2, 3, 3), 10))
        np.testing.assert_array_equal(self.uploads[1][0][:, :, :3], rgb)
        self.assertIs(self.screen._texture_rgba, self.uploads[1][0])

    def test_invalid_buffers_never_reach_native_provider(self):
        for image in (np.zeros((2, 3, 3), dtype=np.float32),
                      np.zeros((2, 3), dtype=np.uint8),
                      np.zeros((0, 3, 3), dtype=np.uint8),
                      np.zeros((2, 3, 4), dtype=np.uint8)):
            with self.subTest(shape=image.shape), self.assertRaises(ValueError):
                self.screen.update_texture(image)
        self.screen.update_texture(None)
        self.assertEqual(self.uploads, [])


if __name__ == "__main__":
    unittest.main()
