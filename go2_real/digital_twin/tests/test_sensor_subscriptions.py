"""Binary sensor layouts and receive failures, independent of Isaac Sim/ROS."""

from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sensor_subscriptions import SensorStream, SensorSubscriptions, decode_depth_m, decode_lidar_xyz


def header(frame="test_frame"):
    return SimpleNamespace(frame_id=frame, stamp=SimpleNamespace(sec=12, nanosec=34))


def depth_msg(data, width, height=1, encoding="16UC1", step=None, bigendian=False):
    itemsize = 2 if encoding == "16UC1" else 4
    return SimpleNamespace(data=data, width=width, height=height, encoding=encoding,
                           step=step if step is not None else width * itemsize,
                           is_bigendian=bigendian, header=header())


def field(name, offset, datatype=7):
    return SimpleNamespace(name=name, offset=offset, datatype=datatype, count=1)


class SensorDecodingTests(unittest.TestCase):
    def test_depth_uint16_big_endian_with_row_padding(self):
        msg = depth_msg(struct.pack(">HHHHHH", 1000, 0, 9999, 2500, 4200, 9999),
                        width=2, height=2, step=6, bigendian=True)
        depth = decode_depth_m(msg)
        np.testing.assert_allclose(depth, [[1.0, np.nan], [2.5, 4.2]], equal_nan=True)

    def test_float_depth_invalid_values_and_input_unchanged(self):
        data = struct.pack("<ffffff", 1.25, float("nan"), float("inf"), 0, -1, 3)
        msg = depth_msg(data, width=3, height=2, encoding="32FC1")
        np.testing.assert_allclose(decode_depth_m(msg), [[1.25, np.nan, np.nan], [np.nan, np.nan, 3]], equal_nan=True)
        self.assertEqual(msg.data, data)

    def test_malformed_depth_is_rejected(self):
        for msg in (depth_msg(b"\x00\x01", width=2),
                    depth_msg(b"\x00" * 4, width=2, step=2),
                    depth_msg(b"\x00" * 6, width=2, encoding="rgb8")):
            with self.subTest(msg=msg), self.assertRaises(ValueError):
                decode_depth_m(msg)

    def test_cloud_big_endian_reordered_fields_row_padding_and_nonfinite(self):
        data = bytearray(160)
        expected = [(1, 2, 3), (4, 5, 6), (float("nan"), 7, 8), (9, 10, float("inf"))]
        for i, (x, y, z) in enumerate(expected):
            offset = (i // 2) * 80 + (i % 2) * 32
            struct.pack_into(">f", data, offset, y)
            struct.pack_into(">f", data, offset + 8, x)
            struct.pack_into(">f", data, offset + 16, z)
        before = bytes(data)
        msg = SimpleNamespace(data=data, width=2, height=2, point_step=32, row_step=80,
                              is_bigendian=True, is_dense=True,
                              fields=[field("z", 16), field("ring", 24, 4), field("x", 8), field("y", 0)])
        np.testing.assert_array_equal(decode_lidar_xyz(msg), [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(bytes(data), before)

    def test_cloud_mixed_float_types(self):
        msg = SimpleNamespace(data=struct.pack("<dfd", 1.5, 2.5, 3.5), width=1, height=1,
                              point_step=20, row_step=20, is_bigendian=False,
                              fields=[field("x", 0, 8), field("y", 8), field("z", 12, 8)])
        np.testing.assert_array_equal(decode_lidar_xyz(msg), [[1.5, 2.5, 3.5]])

    def test_empty_and_malformed_clouds(self):
        self.assertEqual(decode_lidar_xyz(SimpleNamespace(width=0, height=1)).shape, (0, 3))
        base = dict(data=bytes(12), width=1, height=1, point_step=12, row_step=12,
                    is_bigendian=False, fields=[field("x", 0), field("y", 4), field("z", 8)])
        for changes in (dict(data=bytes(8)), dict(row_step=8),
                        dict(fields=[field("x", 0), field("y", 4)]),
                        dict(fields=[field("x", 0), field("y", 4), field("z", 10)])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decode_lidar_xyz(SimpleNamespace(**(base | changes)))

    def test_decode_error_keeps_last_good_sample_and_recovers(self):
        stream = SensorStream("/test/depth")
        good = depth_msg(struct.pack("<H", 1200), width=1)
        stream.receive(good, decode_depth_m)
        last_good = stream.data
        stream.receive(depth_msg(b"", width=1), decode_depth_m)
        self.assertEqual((stream.received, stream.decoded, stream.errors), (2, 1, 1))
        self.assertIs(stream.data, last_good)
        self.assertIn("Truncated", stream.last_error)
        stream.receive(good, decode_depth_m)
        self.assertIsNone(stream.last_error)
        self.assertEqual(stream.decoded, 2)

    def test_diagnostics_distinguish_missing_stale_and_empty(self):
        sensors = SensorSubscriptions.__new__(SensorSubscriptions)
        sensors.started = time.monotonic()
        sensors.depth = SensorStream("/test/depth")
        sensors.lidar = SensorStream("/test/lidar")
        self.assertEqual(sensors.snapshot()["depth"]["status"], "WAITING")
        sensors.depth.receive(depth_msg(struct.pack("<H", 0), width=1), decode_depth_m)
        self.assertEqual(sensors.snapshot()["depth"]["status"], "EMPTY")
        sensors.depth.receive(depth_msg(struct.pack("<H", 1000), width=1), decode_depth_m)
        self.assertEqual(sensors.snapshot()["depth"]["status"], "OK")
        sensors.depth.last_rx -= 3
        self.assertEqual(sensors.snapshot()["depth"]["status"], "STALE")
        sensors.depth.receive(depth_msg(b"", width=1), decode_depth_m)
        self.assertEqual(sensors.snapshot()["depth"]["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
