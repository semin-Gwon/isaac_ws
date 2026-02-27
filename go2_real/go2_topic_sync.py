#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
import os
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CompressedImage, Image, CameraInfo
import tf2_ros
from geometry_msgs.msg import TransformStamped
# from cv_bridge import CvBridge # Removed dependency
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
        # 1. Odometry Sync & TF
        # Best effort is safer across mixed publishers.
        odom_qos = qos_best_effort
        self.subs_odom = []
        self.odom_topics = [
            '/utlidar/robot_odom',
            '/my_go2/robot_odom',
            '/uslam/localization/odom',
            '/uslam/frontend/odom',
        ]
        for topic in self.odom_topics:
            self.subs_odom.append(
                self.create_subscription(Odometry, topic, self.odom_callback, odom_qos)
            )
        self.pub_odom = self.create_publisher(Odometry, '/utlidar/robot_odom_sync', 10)

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
            self.subs_rgb_comp.append(
                self.create_subscription(
                    CompressedImage,
                    topic,
                    self.rgb_compressed_callback,
                    qos_best_effort,
                )
            )
        for topic in self.rgb_raw_topics:
            self.subs_rgb_raw.append(
                self.create_subscription(
                    Image,
                    topic,
                    self.rgb_raw_callback,
                    qos_best_effort,
                )
            )
        # RTAB-Map launch remap target (raw)
        self.pub_rgb_sync = self.create_publisher(Image, '/my_go2/color/image_raw_sync', 10)
        
        self.stream_stable = False
        
        
        # self.cv_bridge = CvBridge()
        
        
        # [Restoring __init__ flow]

        # 3. Depth Image Sync (Compressed Input -> Raw Output)
        # Depth 입력도 raw/compressed 모두 대응
        self.subs_depth_comp = []
        self.subs_depth_raw = []
        self.depth_comp_topics = [
            '/my_go2/depth/image_rect_raw/compressed',
            '/camera/depth/image_rect_raw/compressed',
        ]
        self.depth_raw_topics = [
            '/my_go2/depth/image_rect_raw',
            '/camera/depth/image_rect_raw',
        ]
        for topic in self.depth_comp_topics:
            self.subs_depth_comp.append(
                self.create_subscription(
                    CompressedImage,
                    topic,
                    self.depth_compressed_callback,
                    qos_best_effort,
                )
            )
        for topic in self.depth_raw_topics:
            self.subs_depth_raw.append(
                self.create_subscription(
                    Image,
                    topic,
                    self.depth_raw_callback,
                    qos_best_effort,
                )
            )
        self.pub_depth = self.create_publisher(Image, '/my_go2/depth/image_rect_raw_sync', 10)

        # 4. Camera Info Sync (Synthesized)
        # Real robot is not publishing info, so we generate it
        self.pub_info = self.create_publisher(CameraInfo, '/my_go2/color/camera_info_sync', 10)
        
        # Store latest generic info
        self.camera_info = self.create_dummy_info()

        self._rgb_count = 0
        self._depth_count = 0
        self._odom_count = 0
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
        self.get_logger().info(f"[sub] rgb(comp) topics: {self.rgb_comp_topics}")
        self.get_logger().info(f"[sub] rgb(raw) topics: {self.rgb_raw_topics}")
        self.get_logger().info(f"[sub] depth(comp) topics: {self.depth_comp_topics}")
        self.get_logger().info(f"[sub] depth(raw) topics: {self.depth_raw_topics}")

    def diag_timer(self):
        self.get_logger().info(
            f"[diag] odom_sync={self._odom_count} rgb_sync={self._rgb_count} depth_sync={self._depth_count}",
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

    def bridge_cv2_to_compressed(self, cv_img):
        # Helper: CV2 -> CompressedImage
        msg = CompressedImage()
        msg.format = "jpeg"
        success, encoded_img = cv2.imencode('.jpg', cv_img)
        if success:
             msg.data = encoded_img.tobytes()
        return msg

    def create_dummy_info(self):
        info = CameraInfo()
        info.header.frame_id = "my_go2_color_optical_frame"
        info.width = 640 # Updated to match Real Robot RGB stream (640x480)
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

    def sync_header(self, header):
        # Overwrite timestamp with current ROS time
        header.stamp = self.get_clock().now().to_msg()
        return header

    def odom_callback(self, msg):
        self._odom_count += 1
        # 1. Republish Odom with new stamp
        current_time = self.get_clock().now().to_msg()
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

    def rgb_compressed_callback(self, msg):
        try:
            cv_img = None
            np_arr = np.frombuffer(msg.data, np.uint8)
            cv_img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if cv_img is None:
                 self.get_logger().warn("RGB decode 실패: compressed image", throttle_duration_sec=2.0)
                 return
                 
            # Create ROS Image msg
            header = Header()
            header.stamp = self.get_clock().now().to_msg()
            header.frame_id = "my_go2_color_optical_frame"
            
            raw_msg = self.bridge_cv2_to_imgmsg(cv_img, "bgr8")
            raw_msg.header = header
            self.pub_rgb_sync.publish(raw_msg)
            
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
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "my_go2_color_optical_frame"
        self.pub_rgb_sync.publish(msg)
        self.camera_info.header = msg.header
        self.pub_info.publish(self.camera_info)
        self._rgb_count += 1

    def depth_compressed_callback(self, msg):
        # Sync Header
        synced_header = self.sync_header(msg.header)
        synced_header.frame_id = "my_go2_color_optical_frame"

        try:
            # Decompress Depth
            # Manual Decompress
            np_arr = np.frombuffer(msg.data, np.uint8)
            cv_image = cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED) # Passthrough
            
            # Convert back to ROS Image (Raw) with RTAB-Map compatible depth encodings.
            # Avoid "passthrough", as rtabmap/cv_bridge cannot use it here.
            if cv_image is None:
                self.get_logger().error("Depth decode 실패: compressed depth image is None")
                return
            if cv_image.dtype == np.uint16:
                encoding = "16UC1"
            elif cv_image.dtype == np.float32:
                encoding = "32FC1"
            elif cv_image.dtype == np.uint8:
                # Some devices publish visualization depth as uint8 in compressed stream.
                # Ignore it and rely on raw depth topic for metric depth.
                self.get_logger().warn(
                    "Compressed depth is uint8 (non-metric), skipping this frame.",
                    throttle_duration_sec=2.0,
                )
                return
            else:
                self.get_logger().error(
                    f"Unsupported depth dtype from compressed stream: {cv_image.dtype}"
                )
                return
                 
            raw_msg = self.bridge_cv2_to_imgmsg(cv_image, encoding)
            raw_msg.header = synced_header
            
            self.pub_depth.publish(raw_msg)
            self._depth_count += 1
            
        except Exception as e:
            self.get_logger().error(f'Failed to decompress depth: {e}')

    def depth_raw_callback(self, msg: Image):
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "my_go2_color_optical_frame"
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
