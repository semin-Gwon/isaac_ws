#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
import os
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CompressedImage, Image, CameraInfo, Imu
import tf2_ros
from geometry_msgs.msg import TransformStamped
from std_msgs.msg import Header
import cv2
import numpy as np

class TopicSync(Node):
    def __init__(self):
        super().__init__('topic_sync')

        # TF publisher
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)
        self.static_tf_broadcaster = tf2_ros.StaticTransformBroadcaster(self)

        # QoS Profiles
        qos_best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        # 1. Odometry Sync & TF
        # Best effort is safer across mixed publishers.
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

        # 1.5 IMU Sync
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

        # 2. RGB Image Sync (Compressed Input & Raw Output)
        # Bridge publishes as Reliable/BestEffort mixed, typically BestEffort for images is safer or we match bridge
        # Bridge info said: CompressedImage Publisher is RELIABLE.
        # But let's try BestEffort if Reliable fails, or stay Reliable. 
        # The 'ros2 topic info' said Publisher: RELIABLE. So we MUST use RELIABLE or SYSTEM_DEFAULT (which is usually reliable).
        # Wait, if Publisher is Reliable, Subscriber BestEffort is compatible? NO.
        # Pub: Reliable -> Sub: Reliable (OK)
        # Pub: Reliable -> Sub: BestEffort (OK - Sub takes what it gets)
        # Pub: BestEffort -> Sub: Reliable (INCOMPATIBLE)
        # So BestEffort Subscription is SAFER because it matches both.
        # RGB 입력은 환경별로 raw/compressed가 다를 수 있어 둘 다 구독
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
                    self.create_subscription(
                        CompressedImage,
                        topic,
                        self.rgb_compressed_callback,
                        qos,
                    )
                )
        for topic in self.rgb_raw_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_rgb_raw.append(
                    self.create_subscription(
                        Image,
                        topic,
                        self.rgb_raw_callback,
                        qos,
                    )
                )
        # RTAB-Map launch remap target (raw)
        # RGB only: try RELIABLE to reduce visible frame drop/flicker in RViz.
        self.pub_rgb_sync = self.create_publisher(Image, '/my_go2/color/image_raw_sync', qos_reliable)
        
        # 3. Depth Image Sync (Raw Input -> Raw Output)
        self.subs_depth_raw = []
        self.depth_raw_topics = [
            '/my_go2/depth/image_rect_raw',
            '/camera/depth/image_rect_raw',
        ]
        for topic in self.depth_raw_topics:
            for qos in (qos_best_effort, qos_reliable):
                self.subs_depth_raw.append(
                    self.create_subscription(
                        Image,
                        topic,
                        self.depth_raw_callback,
                        qos,
                    )
                )
        self.pub_depth = self.create_publisher(Image, '/my_go2/depth/image_rect_raw_sync', qos_reliable)

        # 4. Camera Info Sync (Synthesized)
        # Real robot is not publishing info, so we generate it
        self.pub_info = self.create_publisher(CameraInfo, '/my_go2/color/camera_info_sync', qos_best_effort)
        
        # Store latest generic info
        self.camera_info = self.create_dummy_info()

        self._rgb_count = 0
        self._depth_count = 0
        self._odom_count = 0
        self._imu_count = 0
        self._last_odom_stamp = None
        self._last_odom_pub_ns = 0
        self._odom_pub_period_ns = int(1e9 / 60.0)  # cap odom/tf republish at 60 Hz
        self.create_timer(2.0, self.diag_timer)
        self.create_timer(0.2, self.publish_camera_tf_dynamic)
        self.publish_static_tf()

        self.get_logger().info(
            "Topic Sync Node Started: Bridging Real Robot Data (Mixed Types + Synthesized Info)"
        )
        env_keys = [
            "RMW_IMPLEMENTATION",
            "ROS_DOMAIN_ID",
            "CYCLONEDDS_URI",
            "ROS_LOCALHOST_ONLY",
            "ROS_NAMESPACE",
            "ROS_AUTOMATIC_DISCOVERY_RANGE",
            "ROS_STATIC_PEERS",
        ]
        for key in env_keys:
            self.get_logger().info(f"[env] {key}={os.environ.get(key, '(unset)')}")
        self.get_logger().info(f"[sub] odom topics: {self.odom_topics}")
        self.get_logger().info(f"[sub] imu topics: {self.imu_topics}")
        self.get_logger().info(f"[sub] rgb(comp) topics: {self.rgb_comp_topics}")
        self.get_logger().info(f"[sub] rgb(raw) topics: {self.rgb_raw_topics}")
        self.get_logger().info(f"[sub] depth(raw) topics: {self.depth_raw_topics}")

    def diag_timer(self):
        self.get_logger().info(
            f"[diag] odom_sync={self._odom_count} imu_sync={self._imu_count} rgb_sync={self._rgb_count} depth_sync={self._depth_count}",
            throttle_duration_sec=2.0,
        )

    def publish_static_tf(self):
        t_cam = TransformStamped()
        t_cam.header.stamp = self.get_clock().now().to_msg()
        t_cam.header.frame_id = "base_link"
        t_cam.child_frame_id = "my_go2_color_optical_frame"
        t_cam.transform.translation.x = 0.3
        t_cam.transform.translation.y = 0.0
        t_cam.transform.translation.z = 0.1
        t_cam.transform.rotation.x = -0.5
        t_cam.transform.rotation.y = 0.5
        t_cam.transform.rotation.z = -0.5
        t_cam.transform.rotation.w = 0.5
        self.static_tf_broadcaster.sendTransform(t_cam)

    def publish_camera_tf_dynamic(self):
        # Also broadcast camera TF on /tf periodically to avoid transient TF lookup failures.
        stamp = self._last_odom_stamp if self._last_odom_stamp is not None else self.get_clock().now().to_msg()
        self.publish_camera_tf_with_stamp(stamp)

    def publish_camera_tf_with_stamp(self, stamp):
        t_cam = TransformStamped()
        t_cam.header.stamp = stamp
        t_cam.header.frame_id = "base_link"
        t_cam.child_frame_id = "my_go2_color_optical_frame"
        t_cam.transform.translation.x = 0.3
        t_cam.transform.translation.y = 0.0
        t_cam.transform.translation.z = 0.1
        t_cam.transform.rotation.x = -0.5
        t_cam.transform.rotation.y = 0.5
        t_cam.transform.rotation.z = -0.5
        t_cam.transform.rotation.w = 0.5
        self.tf_broadcaster.sendTransform(t_cam)

    def bridge_cv2_to_imgmsg(self, cv_img, encoding="bgr8"):
        # Helper: CV2 -> ROS Image
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
        info.header.frame_id = "my_go2_color_optical_frame"
        info.width = 640
        info.height = 480
        
        # Approximate Intake:
        # Assuming 60 deg FOV horizontally for 640 px width
        # fx = width / (2 * tan(FOV/2))
        # fx ~= 640 / (2 * tan(30)) ~= 640 / 1.15 ~= 554
        # Let's use a standard value or scale from previous 600 (at 1280) -> 300 (at 640)
        # 300 seems small for 640 width (implied FOV ~93 deg). 600 at 1280 is ~93 deg.
        # Let's stick with the scaled value ~300-554.
        # Let's try 320 (FOV ~90) for now.
        fx = 554.0 # Approx 60 deg FOV
        fy = 554.0
        cx = 320.0
        cy = 240.0
        
        info.distortion_model = "plumb_bob"
        info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        info.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        info.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return info

    def update_camera_info_resolution(self, width: int, height: int):
        # Keep intrinsics roughly consistent by scaling from the original 640x480 model.
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

    def sync_header(self, header):
        # Keep message timestamps aligned with odom/tf time when available.
        header.stamp = self._last_odom_stamp if self._last_odom_stamp is not None else self.get_clock().now().to_msg()
        return header

    def get_sync_stamp_or_none(self):
        return self._last_odom_stamp

    def odom_callback(self, msg):
        self._odom_count += 1
        current_time = self.get_clock().now().to_msg()
        self._last_odom_stamp = current_time

        now_ns = self.get_clock().now().nanoseconds
        if (now_ns - self._last_odom_pub_ns) < self._odom_pub_period_ns:
            return
        self._last_odom_pub_ns = now_ns

        # 1. Republish Odom with new stamp
        msg.header.stamp = current_time
        self.pub_odom.publish(msg)

        # 2. Publish TF (odom -> base_link) using same new stamp
        t = TransformStamped()
        t.header.stamp = current_time
        t.header.frame_id = "odom"
        t.child_frame_id = "base_link"

        t.transform.translation.x = msg.pose.pose.position.x
        t.transform.translation.y = msg.pose.pose.position.y
        t.transform.translation.z = msg.pose.pose.position.z
        t.transform.rotation = msg.pose.pose.orientation
        
        self.tf_broadcaster.sendTransform(t)

    def imu_callback(self, msg: Imu):
        stamp = self._last_odom_stamp if self._last_odom_stamp is not None else self.get_clock().now().to_msg()
        msg.header.stamp = stamp
        if not msg.header.frame_id:
            msg.header.frame_id = "base_link"
        self.pub_imu.publish(msg)
        self._imu_count += 1

    def rgb_compressed_callback(self, msg):
        stamp = self.get_sync_stamp_or_none()
        if stamp is None:
            stamp = self.get_clock().now().to_msg()
            self.get_logger().warn(
                "No odom stamp yet, publishing rgb with current time.",
                throttle_duration_sec=2.0,
            )
        try:
            cv_img = None
            np_arr = np.frombuffer(msg.data, np.uint8)
            cv_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if cv_img is None:
                 self.get_logger().warn("RGB decode 실패: compressed image", throttle_duration_sec=2.0)
                 return
                 
            # Create ROS Image msg
            header = Header()
            header.stamp = stamp
            header.frame_id = "my_go2_color_optical_frame"
            
            raw_msg = self.bridge_cv2_to_imgmsg(cv_img, "bgr8")
            raw_msg.header = header
            self.publish_camera_tf_with_stamp(stamp)
            self.pub_rgb_sync.publish(raw_msg)
            self.update_camera_info_resolution(raw_msg.width, raw_msg.height)
            
            # Log periodically
            self.get_logger().info(f"Relaying Video Frame: {cv_img.shape}", throttle_duration_sec=5.0)
            self._rgb_count += 1
            
        except Exception as e:
            self.get_logger().warn(f"Bridge Error: {e}", throttle_duration_sec=1.0)
        
        # Publish Camera Info synced with RGB
        # Unitree msg has no header, so use the one we generated above
        # If decoding failed, header is undefined. logic flow needs fix.
        if cv_img is not None:
             self.camera_info.header = header
             self.pub_info.publish(self.camera_info)

    def rgb_raw_callback(self, msg: Image):
        try:
            stamp = self.get_sync_stamp_or_none()
            if stamp is None:
                stamp = self.get_clock().now().to_msg()
                self.get_logger().warn(
                    "No odom stamp yet, publishing rgb with current time.",
                    throttle_duration_sec=2.0,
                )
            msg.header.stamp = stamp
            msg.header.frame_id = "my_go2_color_optical_frame"
            self.publish_camera_tf_with_stamp(stamp)
            self.pub_rgb_sync.publish(msg)
            self.update_camera_info_resolution(msg.width, msg.height)
            self.camera_info.header = msg.header
            self.pub_info.publish(self.camera_info)
            self._rgb_count += 1
            self.get_logger().info(
                f"Relaying RGB raw frame: {msg.width}x{msg.height}, enc={msg.encoding}",
                throttle_duration_sec=5.0,
            )
        except Exception as e:
            self.get_logger().error(f"rgb_raw_callback failed: {e}", throttle_duration_sec=1.0)

    def depth_raw_callback(self, msg: Image):
        stamp = self.get_sync_stamp_or_none()
        if stamp is None:
            stamp = self.get_clock().now().to_msg()
            self.get_logger().warn(
                "No odom stamp yet, publishing depth with current time.",
                throttle_duration_sec=2.0,
            )
        msg.header.stamp = stamp
        msg.header.frame_id = "my_go2_color_optical_frame"
        self.publish_camera_tf_with_stamp(stamp)
        # Normalize depth encoding for RTAB-Map compatibility.
        # "passthrough" is not a valid ROS encoding string for consumers.
        if msg.encoding == "passthrough":
            if msg.step == msg.width * 2:
                msg.encoding = "16UC1"
            elif msg.step == msg.width * 4:
                msg.encoding = "32FC1"
            else:
                self.get_logger().warn(
                    f"Unknown passthrough depth layout (step={msg.step}, width={msg.width}), defaulting to 16UC1"
                )
                msg.encoding = "16UC1"
        self.pub_depth.publish(msg)
        self._depth_count += 1
    
    # Info callback removed as we synthesize it

def main(args=None):
    rclpy.init(args=args)
    node = TopicSync()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
