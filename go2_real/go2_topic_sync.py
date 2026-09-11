#!/usr/bin/env python3
import argparse
import copy
import os
from collections import deque

import cv2
import numpy as np
import rclpy
import tf2_ros
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, CompressedImage, Image, Imu, PointCloud2


class TopicSync(Node):
    RGB_DEPTH_MAX_DELTA_SEC = 0.08
    RGB_LIDAR_MAX_DELTA_SEC = 0.15
    RGB_ODOM_MAX_DELTA_SEC = 0.10
    BUFFER_RETENTION_SEC = 2.0
    RGB_BUFFER_SIZE = 60
    DEPTH_BUFFER_SIZE = 60
    CLOUD_BUFFER_SIZE = 40
    ODOM_BUFFER_SIZE = 120

    def __init__(self, odom_eval_mode: bool = False, use_lidar: bool = True):
        super().__init__('topic_sync')
        self.odom_eval_mode = odom_eval_mode
        self.use_lidar = use_lidar

        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)
        self.static_tf_broadcaster = tf2_ros.StaticTransformBroadcaster(self)

        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        self.rgb_buffer = deque(maxlen=self.RGB_BUFFER_SIZE)
        self.depth_buffer = deque(maxlen=self.DEPTH_BUFFER_SIZE)
        self.cloud_buffer = deque(maxlen=self.CLOUD_BUFFER_SIZE)
        self.odom_buffer = deque(maxlen=self.ODOM_BUFFER_SIZE)

        self._rgb_count = 0
        self._depth_count = 0
        self._odom_count = 0
        self._imu_count = 0
        self._lidar_cloud_count = 0
        self._rgb_buffered = 0
        self._depth_buffered = 0
        self._cloud_buffered = 0
        self._sync_success_count = 0
        self._sync_drop_count = 0
        self._last_sync_age_sec = -1.0
        self._last_bundle_key = None
        self._last_odom_stamp = None
        self._last_odom_pub_ns = 0
        self._odom_pub_period_ns = int(1e9 / 60.0)

        odom_qos_profiles = [qos_best_effort, qos_reliable]
        self.subs_odom = []
        self.odom_topics = [
            '/utlidar/robot_odom',
            '/my_go2/robot_odom',
            '/uslam/localization/odom',
            '/uslam/frontend/odom',
        ]
        for topic in self.odom_topics:
            for qos in odom_qos_profiles:
                self.subs_odom.append(
                    self.create_subscription(Odometry, topic, self.odom_callback, qos)
                )
        self.pub_odom = self.create_publisher(Odometry, '/utlidar/robot_odom_sync', 10)

        self.subs_imu = []
        self.imu_topics = [
            '/utlidar/imu',
            '/imu/data',
            '/my_go2/imu',
        ]
        for topic in self.imu_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_imu.append(
                    self.create_subscription(Imu, topic, self.imu_callback, qos)
                )
        self.pub_imu = self.create_publisher(Imu, '/utlidar/imu_sync', qos_best_effort)

        self.subs_lidar_cloud = []
        self.lidar_cloud_topics = [
            '/utlidar/cloud',
            '/utlidar/cloud_deskewed',
            '/utlidar/cloud_base',
        ]
        for topic in self.lidar_cloud_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_lidar_cloud.append(
                    self.create_subscription(PointCloud2, topic, self.lidar_cloud_callback, qos)
                )
        self.pub_lidar_cloud = self.create_publisher(PointCloud2, '/utlidar/cloud_sync', qos_best_effort)

        self.subs_rgb_comp = []
        self.subs_rgb_raw = []
        self.rgb_comp_topics = [
            '/my_go2/color/image_raw/compressed',
            '/camera/color/image_raw/compressed',
        ]
        self.rgb_raw_topics = [
            '/my_go2/color/image_raw',
            '/camera/color/image_raw',
        ]
        for topic in self.rgb_comp_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_rgb_comp.append(
                    self.create_subscription(CompressedImage, topic, self.rgb_compressed_callback, qos)
                )
        for topic in self.rgb_raw_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_rgb_raw.append(
                    self.create_subscription(Image, topic, self.rgb_raw_callback, qos)
                )
        self.pub_rgb_sync = self.create_publisher(Image, '/my_go2/color/image_raw_sync', qos_reliable)

        self.subs_depth_raw = []
        self.depth_raw_topics = [
            '/my_go2/depth/image_rect_raw',
            '/camera/depth/image_rect_raw',
        ]
        for topic in self.depth_raw_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_depth_raw.append(
                    self.create_subscription(Image, topic, self.depth_raw_callback, qos)
                )
        self.pub_depth = self.create_publisher(Image, '/my_go2/depth/image_rect_raw_sync', qos_reliable)

        self.subs_info = []
        self.camera_info_topics = [
            '/my_go2/color/camera_info',
            '/camera/color/camera_info',
            '/camera/camera_info',
        ]
        for topic in self.camera_info_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_info.append(
                    self.create_subscription(CameraInfo, topic, self.camera_info_callback, qos)
                )
        self.pub_info = self.create_publisher(CameraInfo, '/my_go2/color/camera_info_sync', qos_best_effort)

        self.camera_info = self.create_dummy_info()
        self._has_real_camera_info = False

        self.create_timer(2.0, self.diag_timer)
        self.create_timer(0.2, self.publish_camera_tf_dynamic)
        self.publish_static_tf()

        self.get_logger().info("Topic Sync Node Started: buffered bundle synchronizer")
        self.get_logger().info(
            f"[mode] odom_eval_mode={self.odom_eval_mode} use_lidar={self.use_lidar}"
        )
        for key in (
            "RMW_IMPLEMENTATION",
            "ROS_DOMAIN_ID",
            "CYCLONEDDS_URI",
            "ROS_LOCALHOST_ONLY",
            "ROS_NAMESPACE",
            "ROS_AUTOMATIC_DISCOVERY_RANGE",
            "ROS_STATIC_PEERS",
        ):
            self.get_logger().info(f"[env] {key}={os.environ.get(key, '(unset)')}")
        self.get_logger().info(f"[sub] odom topics: {self.odom_topics}")
        self.get_logger().info(f"[sub] imu topics: {self.imu_topics}")
        self.get_logger().info(f"[sub] lidar(cloud) topics: {self.lidar_cloud_topics}")
        self.get_logger().info(f"[sub] rgb(comp) topics: {self.rgb_comp_topics}")
        self.get_logger().info(f"[sub] rgb(raw) topics: {self.rgb_raw_topics}")
        self.get_logger().info(f"[sub] depth(raw) topics: {self.depth_raw_topics}")
        self.get_logger().info(f"[sub] camera_info topics: {self.camera_info_topics}")

    def diag_timer(self):
        self.get_logger().info(
            "[diag] "
            f"odom_sync={self._odom_count} imu_sync={self._imu_count} "
            f"rgb_sync={self._rgb_count} depth_sync={self._depth_count} "
            f"lidar_cloud_sync={self._lidar_cloud_count} "
            f"rgb_buffered={self._rgb_buffered} depth_buffered={self._depth_buffered} "
            f"cloud_buffered={self._cloud_buffered} "
            f"sync_success={self._sync_success_count} sync_drop={self._sync_drop_count} "
            f"last_sync_age_sec={self._last_sync_age_sec:.3f}",
            throttle_duration_sec=2.0,
        )

    def publish_static_tf(self):
        t_cam_link = TransformStamped()
        t_cam_link.header.stamp = self.get_clock().now().to_msg()
        t_cam_link.header.frame_id = "base_link"
        t_cam_link.child_frame_id = "camera_link"
        t_cam_link.transform.translation.x = 0.3
        t_cam_link.transform.translation.y = 0.0
        t_cam_link.transform.translation.z = 0.1
        t_cam_link.transform.rotation.w = 1.0

        t_cam = TransformStamped()
        t_cam.header.stamp = self.get_clock().now().to_msg()
        t_cam.header.frame_id = "camera_link"
        t_cam.child_frame_id = "camera_optical_frame"
        t_cam.transform.rotation.x = -0.5
        t_cam.transform.rotation.y = 0.5
        t_cam.transform.rotation.z = -0.5
        t_cam.transform.rotation.w = 0.5
        self.static_tf_broadcaster.sendTransform([t_cam_link, t_cam])

    def publish_camera_tf_dynamic(self):
        stamp = self._last_odom_stamp if self._last_odom_stamp is not None else self.get_clock().now().to_msg()
        self.publish_camera_tf_with_stamp(stamp)

    def publish_camera_tf_with_stamp(self, stamp):
        t_cam_link = TransformStamped()
        t_cam_link.header.stamp = stamp
        t_cam_link.header.frame_id = "base_link"
        t_cam_link.child_frame_id = "camera_link"
        t_cam_link.transform.translation.x = 0.3
        t_cam_link.transform.translation.y = 0.0
        t_cam_link.transform.translation.z = 0.1
        t_cam_link.transform.rotation.w = 1.0

        t_cam = TransformStamped()
        t_cam.header.stamp = stamp
        t_cam.header.frame_id = "camera_link"
        t_cam.child_frame_id = "camera_optical_frame"
        t_cam.transform.rotation.x = -0.5
        t_cam.transform.rotation.y = 0.5
        t_cam.transform.rotation.z = -0.5
        t_cam.transform.rotation.w = 0.5
        self.tf_broadcaster.sendTransform([t_cam_link, t_cam])

    def msg_time_sec(self, msg):
        stamp = msg.header.stamp
        return float(stamp.sec) + float(stamp.nanosec) * 1e-9

    def clone_msg(self, msg):
        return copy.deepcopy(msg)

    def normalize_depth_msg(self, msg: Image):
        normalized = self.clone_msg(msg)
        normalized.header.frame_id = "camera_optical_frame"
        if normalized.encoding == "passthrough":
            if normalized.step == normalized.width * 2:
                normalized.encoding = "16UC1"
            elif normalized.step == normalized.width * 4:
                normalized.encoding = "32FC1"
            else:
                self.get_logger().warn(
                    f"Unknown passthrough depth layout (step={normalized.step}, width={normalized.width}), defaulting to 16UC1"
                )
                normalized.encoding = "16UC1"
        return normalized

    def find_closest(self, buffer, stamp_sec, max_delta_sec):
        best_msg = None
        best_delta = None
        for msg in reversed(buffer):
            delta = abs(self.msg_time_sec(msg) - stamp_sec)
            if best_delta is None or delta < best_delta:
                best_msg = msg
                best_delta = delta
            if best_delta is not None and delta > best_delta:
                break
        if best_msg is None or best_delta is None or best_delta > max_delta_sec:
            return None, None
        return best_msg, best_delta

    def prune_old_buffers(self, reference_sec=None):
        if reference_sec is None:
            reference_sec = self.get_clock().now().nanoseconds * 1e-9
        min_sec = reference_sec - self.BUFFER_RETENTION_SEC
        for buffer in (self.rgb_buffer, self.depth_buffer, self.cloud_buffer, self.odom_buffer):
            while buffer and self.msg_time_sec(buffer[0]) < min_sec:
                buffer.popleft()

    def append_buffer(self, buffer, msg):
        buffer.append(msg)
        self.prune_old_buffers(self.msg_time_sec(msg))

    def create_synced_camera_info(self, stamp, width, height):
        if not self._has_real_camera_info:
            self.update_camera_info_resolution(width, height)
        info_msg = self.clone_msg(self.camera_info)
        info_msg.header.stamp = stamp
        info_msg.header.frame_id = "camera_optical_frame"
        info_msg.width = width
        info_msg.height = height
        return info_msg

    def publish_synced_bundle(self, rgb_msg, depth_msg, cloud_msg, odom_msg):
        bundle_stamp = odom_msg.header.stamp if odom_msg is not None else rgb_msg.header.stamp
        rgb_out = self.clone_msg(rgb_msg)
        depth_out = self.clone_msg(depth_msg)
        odom_out = self.clone_msg(odom_msg) if odom_msg is not None else None
        cloud_out = self.clone_msg(cloud_msg) if cloud_msg is not None else None

        rgb_out.header.stamp = bundle_stamp
        rgb_out.header.frame_id = "camera_optical_frame"
        depth_out.header.stamp = bundle_stamp
        depth_out.header.frame_id = "camera_optical_frame"

        if odom_out is not None and not self.odom_eval_mode:
            odom_out.header.stamp = bundle_stamp
        if cloud_out is not None:
            cloud_out.header.stamp = bundle_stamp
            if not cloud_out.header.frame_id:
                cloud_out.header.frame_id = "base_link"

        info_msg = self.create_synced_camera_info(bundle_stamp, rgb_out.width, rgb_out.height)

        self.publish_camera_tf_with_stamp(bundle_stamp)
        self.pub_rgb_sync.publish(rgb_out)
        self.pub_depth.publish(depth_out)
        self.pub_info.publish(info_msg)
        self._rgb_count += 1
        self._depth_count += 1

        if cloud_out is not None:
            self.pub_lidar_cloud.publish(cloud_out)
            self._lidar_cloud_count += 1
        if odom_out is not None:
            self.pub_odom.publish(odom_out)
            if not self.odom_eval_mode:
                self.publish_odom_tf(odom_out, bundle_stamp)

        self._last_sync_age_sec = abs(self.msg_time_sec(rgb_msg) - self.msg_time_sec(odom_msg)) if odom_msg else 0.0
        self._sync_success_count += 1

    def publish_odom_tf(self, odom_msg, stamp):
        t = TransformStamped()
        t.header.stamp = stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link"
        t.transform.translation.x = odom_msg.pose.pose.position.x
        t.transform.translation.y = odom_msg.pose.pose.position.y
        t.transform.translation.z = odom_msg.pose.pose.position.z
        t.transform.rotation = odom_msg.pose.pose.orientation
        self.tf_broadcaster.sendTransform(t)

    def try_publish_bundle(self, rgb_msg):
        rgb_sec = self.msg_time_sec(rgb_msg)
        depth_msg, depth_delta = self.find_closest(
            self.depth_buffer, rgb_sec, self.RGB_DEPTH_MAX_DELTA_SEC
        )
        if depth_msg is None:
            self._sync_drop_count += 1
            return

        cloud_msg = None
        if self.use_lidar:
            cloud_msg, cloud_delta = self.find_closest(
                self.cloud_buffer, rgb_sec, self.RGB_LIDAR_MAX_DELTA_SEC
            )
            if cloud_msg is None:
                self._sync_drop_count += 1
                return

        odom_msg, odom_delta = self.find_closest(
            self.odom_buffer, rgb_sec, self.RGB_ODOM_MAX_DELTA_SEC
        )
        if odom_msg is None:
            odom_msg = self.odom_buffer[-1] if self.odom_buffer else None
        if odom_msg is None:
            self._sync_drop_count += 1
            self.get_logger().warn("Skipping bundle publish: no odom available.", throttle_duration_sec=2.0)
            return

        bundle_key = (
            self.msg_time_sec(rgb_msg),
            self.msg_time_sec(depth_msg),
            self.msg_time_sec(cloud_msg) if cloud_msg is not None else None,
            self.msg_time_sec(odom_msg),
        )
        if bundle_key == self._last_bundle_key:
            return
        self._last_bundle_key = bundle_key

        self.publish_synced_bundle(rgb_msg, depth_msg, cloud_msg, odom_msg)
        self.get_logger().info(
            "Published synced bundle "
            f"(rgb-depth={depth_delta:.3f}s, "
            f"rgb-odom={(odom_delta if odom_delta is not None else abs(rgb_sec - self.msg_time_sec(odom_msg))):.3f}s"
            + (
                f", rgb-cloud={cloud_delta:.3f}s"
                if self.use_lidar and cloud_msg is not None
                else ""
            )
            + ")",
            throttle_duration_sec=1.0,
        )

    def bridge_cv2_to_imgmsg(self, cv_img, encoding="bgr8"):
        msg = Image()
        msg.height = cv_img.shape[0]
        msg.width = cv_img.shape[1]
        msg.encoding = encoding
        msg.is_bigendian = 0
        if len(cv_img.shape) == 2:
            msg.step = cv_img.shape[1] * cv_img.dtype.itemsize
        else:
            msg.step = cv_img.shape[1] * cv_img.shape[2] * cv_img.dtype.itemsize
        msg.data = cv_img.tobytes()
        return msg

    def create_dummy_info(self):
        info = CameraInfo()
        info.header.frame_id = "camera_optical_frame"
        info.width = 640
        info.height = 480
        fx = 554.0
        fy = 554.0
        cx = 320.0
        cy = 240.0
        info.distortion_model = "plumb_bob"
        info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return info

    def update_camera_info_resolution(self, width: int, height: int):
        if width <= 0 or height <= 0:
            return
        if self.camera_info.width == width and self.camera_info.height == height:
            return

        base_w = 640.0
        base_h = 480.0
        sx = float(width) / base_w
        sy = float(height) / base_h
        fx = 554.0 * sx
        fy = 554.0 * sy
        cx = (float(width) - 1.0) / 2.0
        cy = (float(height) - 1.0) / 2.0

        self.camera_info.width = int(width)
        self.camera_info.height = int(height)
        self.camera_info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        self.camera_info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        self.get_logger().info(
            f"Updated CameraInfo resolution to {width}x{height} (fx={fx:.1f}, fy={fy:.1f})"
        )

    def camera_info_callback(self, msg: CameraInfo):
        self.camera_info = self.clone_msg(msg)
        self.camera_info.header.frame_id = "camera_optical_frame"
        self._has_real_camera_info = True

    def odom_callback(self, msg):
        self._odom_count += 1
        odom_in = self.clone_msg(msg)
        current_time = self.get_clock().now().to_msg()
        if odom_in.header.stamp.sec == 0 and odom_in.header.stamp.nanosec == 0:
            odom_in.header.stamp = current_time
        self.append_buffer(self.odom_buffer, odom_in)

        self._last_odom_stamp = odom_in.header.stamp if self.odom_eval_mode else current_time
        now_ns = self.get_clock().now().nanoseconds
        if (not self.odom_eval_mode) and ((now_ns - self._last_odom_pub_ns) < self._odom_pub_period_ns):
            return
        self._last_odom_pub_ns = now_ns

    def imu_callback(self, msg: Imu):
        imu_msg = self.clone_msg(msg)
        stamp = self._last_odom_stamp if self._last_odom_stamp is not None else self.get_clock().now().to_msg()
        imu_msg.header.stamp = stamp
        if not imu_msg.header.frame_id:
            imu_msg.header.frame_id = "base_link"
        self.pub_imu.publish(imu_msg)
        self._imu_count += 1

    def lidar_cloud_callback(self, msg: PointCloud2):
        cloud_msg = self.clone_msg(msg)
        if cloud_msg.header.stamp.sec == 0 and cloud_msg.header.stamp.nanosec == 0:
            cloud_msg.header.stamp = self.get_clock().now().to_msg()
        if not cloud_msg.header.frame_id:
            cloud_msg.header.frame_id = "base_link"
        self.append_buffer(self.cloud_buffer, cloud_msg)
        self._cloud_buffered += 1

    def rgb_compressed_callback(self, msg):
        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            cv_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if cv_img is None:
                self.get_logger().warn("RGB decode failed: compressed image", throttle_duration_sec=2.0)
                return
            raw_msg = self.bridge_cv2_to_imgmsg(cv_img, "bgr8")
            raw_msg.header.stamp = msg.header.stamp if (msg.header.stamp.sec or msg.header.stamp.nanosec) else self.get_clock().now().to_msg()
            raw_msg.header.frame_id = "camera_optical_frame"
            self.update_camera_info_resolution(raw_msg.width, raw_msg.height)
            self.append_buffer(self.rgb_buffer, raw_msg)
            self._rgb_buffered += 1
            self.try_publish_bundle(raw_msg)
        except Exception as e:
            self.get_logger().warn(f"Bridge Error: {e}", throttle_duration_sec=1.0)

    def rgb_raw_callback(self, msg: Image):
        try:
            rgb_msg = self.clone_msg(msg)
            if rgb_msg.header.stamp.sec == 0 and rgb_msg.header.stamp.nanosec == 0:
                rgb_msg.header.stamp = self.get_clock().now().to_msg()
            rgb_msg.header.frame_id = "camera_optical_frame"
            self.update_camera_info_resolution(rgb_msg.width, rgb_msg.height)
            self.append_buffer(self.rgb_buffer, rgb_msg)
            self._rgb_buffered += 1
            self.try_publish_bundle(rgb_msg)
        except Exception as e:
            self.get_logger().error(f"rgb_raw_callback failed: {e}", throttle_duration_sec=1.0)

    def depth_raw_callback(self, msg: Image):
        depth_msg = self.normalize_depth_msg(msg)
        if depth_msg.header.stamp.sec == 0 and depth_msg.header.stamp.nanosec == 0:
            depth_msg.header.stamp = self.get_clock().now().to_msg()
        self.append_buffer(self.depth_buffer, depth_msg)
        self._depth_buffered += 1


def main(args=None):
    parser = argparse.ArgumentParser(description='Topic sync bridge for Go2 inputs.')
    parser.add_argument(
        '--odom-eval-mode',
        choices=['true', 'false'],
        default='false',
        help='When true, preserve odom stamps and skip odom->base_link TF republish.',
    )
    parser.add_argument(
        '--use-lidar',
        choices=['true', 'false'],
        default='true',
        help='When true, require a LiDAR cloud in each synchronized bundle.',
    )
    known_args, ros_args = parser.parse_known_args(args=args)

    rclpy.init(args=ros_args)
    node = TopicSync(
        odom_eval_mode=(known_args.odom_eval_mode == 'true'),
        use_lidar=(known_args.use_lidar == 'true'),
    )
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
