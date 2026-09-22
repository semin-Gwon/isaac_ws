"""Depth/LiDAR subscriptions shared by Isaac Sim and the standalone receive check."""

import argparse
from dataclasses import dataclass
import json
import time

import numpy as np


def add_sensor_arguments(parser):
    parser.add_argument(
        "--depth-topic", default="/camera/aligned_depth_to_color/image_raw",
        help="Depth sensor_msgs/msg/Image topic (16UC1 or 32FC1)",
    )
    parser.add_argument(
        "--lidar-topic", default="/utlidar/cloud_base",
        help="LiDAR sensor_msgs/msg/PointCloud2 topic; coordinates stay in header.frame_id",
    )


def decode_depth_m(msg):
    """Decode REP-118 depth to meters, honoring byte order and row padding."""
    # https://github.com/ros-infrastructure/rep/blob/master/rep-0118.rst
    encoding = msg.encoding.upper()
    formats = {"16UC1": "u2", "32FC1": "f4"}
    if encoding not in formats:
        raise ValueError(f"Unsupported depth encoding: {msg.encoding}")
    dtype = np.dtype((">" if msg.is_bigendian else "<") + formats[encoding])
    h, w = int(msg.height), int(msg.width)
    if h <= 0 or w <= 0 or msg.step < w * dtype.itemsize:
        raise ValueError("Invalid depth dimensions or row step")
    if len(msg.data) < h * msg.step:
        raise ValueError("Truncated depth buffer")
    depth = np.ndarray(
        (h, w), dtype=dtype, buffer=msg.data, strides=(msg.step, dtype.itemsize)
    ).astype(np.float32, copy=True)
    if encoding == "16UC1":
        depth *= np.float32(0.001)
    depth[~np.isfinite(depth) | (depth <= 0)] = np.nan
    return depth


def decode_lidar_xyz(msg):
    """Read finite XYZ points without changing their frame or the input buffer."""
    h, w = int(msg.height), int(msg.width)
    if h == 0 or w == 0:
        return np.empty((0, 3), dtype=np.float32)
    if msg.point_step <= 0 or msg.row_step < w * msg.point_step:
        raise ValueError("Invalid point cloud point/row step")
    if len(msg.data) < h * msg.row_step:
        raise ValueError("Truncated point cloud buffer")
    fields = {field.name: field for field in msg.fields}
    formats, offsets = [], []
    for name in ("x", "y", "z"):
        field = fields.get(name)
        # PointField.FLOAT32 = 7, PointField.FLOAT64 = 8.
        if field is None or field.count != 1 or field.datatype not in (7, 8):
            raise ValueError(f"Point cloud needs a scalar float field: {name}")
        dtype = np.dtype((">" if msg.is_bigendian else "<") + {7: "f4", 8: "f8"}[field.datatype])
        if field.offset < 0 or field.offset + dtype.itemsize > msg.point_step:
            raise ValueError(f"Point field exceeds point_step: {name}")
        formats.append(dtype)
        offsets.append(field.offset)
    dtype = np.dtype(dict(names=["x", "y", "z"], formats=formats,
                          offsets=offsets, itemsize=msg.point_step))
    points = np.ndarray((h, w), dtype=dtype, buffer=msg.data,
                        strides=(msg.row_step, msg.point_step))
    xyz = np.stack([points[name].reshape(-1) for name in ("x", "y", "z")], axis=1)
    return xyz[np.isfinite(xyz).all(axis=1)]


@dataclass
class SensorStream:
    topic: str
    received: int = 0
    decoded: int = 0
    errors: int = 0
    last_rx: float | None = None
    last_decode: float | None = None
    last_error: str | None = None
    data: np.ndarray | None = None
    header: object = None
    encoding: str = ""
    total_points: int = 0

    def receive(self, msg, decoder):
        self.received += 1
        self.last_rx = time.monotonic()
        try:
            data = decoder(msg)
        except (ValueError, TypeError, BufferError, OverflowError) as error:
            self.errors += 1
            self.last_error = str(error)
            return
        self.data = data
        self.header = msg.header
        self.encoding = getattr(msg, "encoding", "")
        self.total_points = msg.width * msg.height
        self.decoded += 1
        self.last_decode = time.monotonic()
        self.last_error = None


class SensorSubscriptions:
    def __init__(self, node, depth_topic, lidar_topic):
        # Import ROS after Isaac Sim starts when used by go2_visualize.py.
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
        from sensor_msgs.msg import Image, PointCloud2

        self.started = time.monotonic()
        self.depth = SensorStream(depth_topic)
        self.lidar = SensorStream(lidar_topic)
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST, depth=1)
        self.sub_depth = node.create_subscription(
            Image, depth_topic, self.depth_callback, qos
        )
        self.sub_lidar = node.create_subscription(
            PointCloud2, lidar_topic, self.lidar_callback, qos
        )
        print(f"[DEPTH] Subscribing to {depth_topic}", flush=True)
        print(f"[LIDAR] Subscribing to {lidar_topic}", flush=True)

    def depth_callback(self, msg):
        self.depth.receive(msg, decode_depth_m)

    def lidar_callback(self, msg):
        self.lidar.receive(msg, decode_lidar_xyz)

    def snapshot(self):
        now = time.monotonic()
        report = {}
        for name, stream in (("depth", self.depth), ("lidar", self.lidar)):
            age = None if stream.last_rx is None else now - stream.last_rx
            valid = 0
            if stream.data is not None:
                valid = int(np.isfinite(stream.data).sum()) if name == "depth" else len(stream.data)
            status = "OK"
            if age is None:
                status = "WAITING"
            elif age > 2.0:
                status = "STALE"
            elif stream.last_error:
                status = "ERROR"
            elif valid == 0:
                status = "EMPTY"
            info = dict(topic=stream.topic, status=status, received=stream.received,
                        decoded=stream.decoded, errors=stream.errors,
                        avg_hz=round(stream.received / max(now - self.started, 1e-6), 2),
                        last_rx_age_sec=None if age is None else round(age, 3),
                        last_decode_age_sec=None if stream.last_decode is None else round(now - stream.last_decode, 3),
                        frame_id=stream.header.frame_id if stream.header else None,
                        stamp_sec=stream.header.stamp.sec if stream.header else None,
                        stamp_nanosec=stream.header.stamp.nanosec if stream.header else None,
                        last_error=stream.last_error)
            if name == "depth":
                finite = stream.data[np.isfinite(stream.data)] if valid else None
                info.update(encoding=stream.encoding, shape=list(stream.data.shape) if stream.data is not None else None,
                            valid_pixels=valid,
                            range_m=[round(float(finite.min()), 3), round(float(finite.max()), 3)] if valid else None)
            else:
                info.update(total_points=stream.total_points, valid_points=valid)
            report[name] = info
        return report

    def log_diagnostics(self):
        for name, info in self.snapshot().items():
            detail = (f"shape={info['shape']} {info['encoding']} valid={info['valid_pixels']} range_m={info['range_m']}"
                      if name == "depth" else f"points={info['valid_points']}/{info['total_points']}")
            print(f"[{name.upper()}] {info['status']} {info['topic']} "
                  f"rx={info['received']} decoded={info['decoded']} avg_hz={info['avg_hz']} "
                  f"age={info['last_rx_age_sec']}s frame={info['frame_id']} {detail} "
                  f"errors={info['errors']}"
                  + (f" last_error={info['last_error']}" if info['last_error'] else ""), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Check live depth/LiDAR subscriptions without starting Isaac Sim")
    add_sensor_arguments(parser)
    parser.add_argument("--seconds", type=float, default=10.0, help="Receive duration (default: 10 seconds)")
    args = parser.parse_args()
    if not np.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be a positive finite number")

    import rclpy
    from rclpy.node import Node

    rclpy.init(args=[])
    node = Node("go2_sensor_check", enable_rosout=False, start_parameter_services=False)
    try:
        sensors = SensorSubscriptions(node, args.depth_topic, args.lidar_topic)
        node.create_timer(2.0, sensors.log_diagnostics)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=min(0.1, max(0.0, deadline - time.monotonic())))
        report = sensors.snapshot()
        print(json.dumps(report, indent=2), flush=True)
        success = all(info["status"] == "OK" and info["errors"] == 0 for info in report.values())
        print("Sensor receive check: " + ("PASS" if success else "FAIL"), flush=True)
        return 0 if success else 1
    except KeyboardInterrupt:
        return 130
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
