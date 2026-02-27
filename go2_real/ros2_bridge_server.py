#!/usr/bin/env python3
"""
Go2 LowState → JointState 변환 브리지 서버
실제 로봇의 /lf/lowstate를 받아 표준 /joint_states로 변환하여 Isaac Sim과 통신합니다.

[실행 환경]
- RoboStack Conda 환경 (ros-humble)
- unitree_go 패키지 설치 필수

[기능]
1. /lf/lowstate (unitree_go/msg/LowState) 구독
2. sensor_msgs/msg/JointState 변환 및 발행
3. (옵션) /utlidar/robot_odom 릴레이
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from sensor_msgs.msg import JointState
from unitree_go.msg import LowState
from nav_msgs.msg import Odometry
from tf2_msgs.msg import TFMessage
from geometry_msgs.msg import TransformStamped
import numpy as np
import time
import os
import sys
import math


def check_environment() -> None:
    """실행 환경을 검증하고 필수 환경변수를 자동 설정합니다.
    
    RoboStack 환경에서 go2 별칭을 사용하면 다른 워크스페이스가 소싱되므로,
    DDS 관련 환경변수만 직접 설정합니다.
    
    NOTE: os.environ 설정은 rclpy.init() 이전에 수행해야 적용됩니다.
    """
    # CycloneDDS RMW 자동 설정
    rmw = os.environ.get('RMW_IMPLEMENTATION', '')
    if rmw != 'rmw_cyclonedds_cpp':
        os.environ['RMW_IMPLEMENTATION'] = 'rmw_cyclonedds_cpp'
        print("[자동설정] RMW_IMPLEMENTATION=rmw_cyclonedds_cpp")
    
    # CycloneDDS 설정 파일
    # source setup.bash 등으로 이미 설정된 경우 그 값을 존중
    cyclone_uri = os.environ.get('CYCLONEDDS_URI', '')
    if cyclone_uri:
        print(f"[환경유지] CYCLONEDDS_URI={cyclone_uri}")
    else:
        # 미설정 시 브리지 전용 DDS 설정을 우선 사용
        shared_xml = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cyclonedds_bridge.xml')
        default_xml = os.path.expanduser('~/cyclonedds.xml')
        if os.path.exists(shared_xml):
            os.environ['CYCLONEDDS_URI'] = f'file://{shared_xml}'
            print(f"[자동설정] CYCLONEDDS_URI=file://{shared_xml}")
        elif os.path.exists(default_xml):
            os.environ['CYCLONEDDS_URI'] = f'file://{default_xml}'
            print(f"[자동설정] CYCLONEDDS_URI=file://{default_xml}")
        else:
            print("⚠️  경고: cyclonedds_bridge.xml 및 ~/cyclonedds.xml 파일이 없습니다.")


class Go2BridgeServer(Node):
    def __init__(self):
        super().__init__('go2_bridge_server')
        
        # [진단] DDS 환경 확인
        cyclone_uri = os.environ.get('CYCLONEDDS_URI', '(미설정)')
        domain_id = os.environ.get('ROS_DOMAIN_ID', '0')
        rmw_impl = os.environ.get('RMW_IMPLEMENTATION', '(기본값)')
        self.get_logger().info(f"[환경] RMW: {rmw_impl}, DOMAIN_ID: {domain_id}")
        self.get_logger().info(f"[환경] CYCLONEDDS_URI: {cyclone_uri}")
        
        # QoS 설정
        # Unitree Go2는 bare DDS 앱으로, RELIABLE Publisher이지만
        # ACK 핸들링이 불완전하여 BEST_EFFORT Subscriber가 더 안정적임
        # (CycloneDDS에서 RELIABLE→BEST_EFFORT 수신은 허용됨)
        qos_profile = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # 1. 구독자 (Subscriber)
        self.get_logger().info("[설정] /lf/lowstate 구독 중 (QoS: BEST_EFFORT)...")
        self.sub_lowstate = self.create_subscription(
            LowState, 
            '/lf/lowstate', 
            self.lowstate_callback, 
            qos_profile
        )
        
        # Odom 데이터 릴레이용 구독
        self.sub_odom = self.create_subscription(
            Odometry,
            '/utlidar/robot_odom',
            self.odom_callback,
            qos_profile
        )

        # 2. 발행자 (Publisher)
        self.pub_joint_state = self.create_publisher(
            JointState, 
            '/joint_states', 
            10
        )
        self.pub_tf = self.create_publisher(
            TFMessage,
            '/tf',
            10
        )
        # Isaac Sim 로컬 소비를 위한 odom 재발행
        self.pub_odom_relay = self.create_publisher(
            Odometry,
            '/my_go2/robot_odom',
            10
        )
        
        # 로봇 관절 이름 정의 (Go2 URDF 순서 준수)
        self.joint_names = [
            "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
            "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
            "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
            "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
        ]
        
        # [진단] 타이머로 수신 상태 주기적 체크 (5초마다)
        self._msg_count = 0
        self.create_timer(5.0, self._check_status)

        # TF 안정화 상태 (odom 노이즈로 인한 튐 방지)
        self._tf_alpha = 0.2  # low-pass 비율 (0~1), 작을수록 더 부드러움
        self._tf_x = None
        self._tf_y = None
        self._tf_yaw = None
        self._tf_z_fixed = None

        # Joint 안정화 상태 (다리 떨림 저감)
        self._joint_alpha = 0.35         # low-pass 비율 (0~1), 클수록 반응 빠름
        self._joint_deadband = 0.0015    # rad 단위 미세 변화 무시
        self._joint_pub_hz = 120.0       # /joint_states 최대 발행 주기 제한
        self._joint_last_pub_t = 0.0
        self._joint_q_filt = None  # np.ndarray(12,)
        
        self.get_logger().info("Go2 Bridge Server Started. Waiting for /lf/lowstate...")
    
    def _check_status(self):
        """5초마다 수신 상태를 로그로 출력 (진단용)"""
        if self._msg_count == 0:
            self.get_logger().warn(
                "[진단] 아직 /lf/lowstate 메시지를 수신하지 못했습니다. "
                "네트워크/DDS 설정을 확인하세요."
            )
        else:
            self.get_logger().info(
                f"[진단] 지금까지 {self._msg_count}개의 lowstate 메시지 수신"
            )

    def lowstate_callback(self, msg: LowState):
        """LowState 메시지를 받아 JointState로 변환하여 발행"""
        # [디버그] 콜백 진입 확인 (1초에 한 번)
        self._msg_count += 1
        self.get_logger().info(
            f"[DEBUG] lowstate_callback 호출됨! motor_state 개수: {len(msg.motor_state)}",
            throttle_duration_sec=1.0
        )
        
        joint_state = JointState()
        joint_state.header.stamp = self.get_clock().now().to_msg()
        joint_state.header.frame_id = "base_link"
        joint_state.name = self.joint_names
        
        # 데이터 추출 (Go2는 12개 모터)
        if len(msg.motor_state) >= 12:
            # 발행률 제한: physics tick보다 빠른 미세 갱신을 줄여 떨림 감소
            now = time.time()
            if (now - self._joint_last_pub_t) < (1.0 / self._joint_pub_hz):
                return

            q_meas = np.zeros(12, dtype=np.float64)
            # NOTE:
            # ROS2SubscribeJointState + IsaacArticulationController 조합에서는
            # 수신된 velocity/effort 배열도 "명령"으로 해석됩니다.
            # LowState의 dq, tau_est는 "측정값"이므로 그대로 넣으면
            # 의도치 않은 토크/속도 명령이 함께 적용되어 자세가 무너질 수 있습니다.
            # 따라서 디지털 트윈 동기화 목적에서는 position만 명령으로 전달합니다.
            
            for i in range(12):
                state = msg.motor_state[i]
                q_meas[i] = float(state.q)
                # measured dq/tau_est are intentionally not forwarded as commands

            # Low-pass filter
            if self._joint_q_filt is None:
                self._joint_q_filt = q_meas.copy()
            else:
                a = self._joint_alpha
                self._joint_q_filt = (1.0 - a) * self._joint_q_filt + a * q_meas

            # Deadband: 미세 진동 억제
            q_cmd = self._joint_q_filt.copy()
            if hasattr(self, "_joint_q_last_cmd"):
                dq = q_cmd - self._joint_q_last_cmd
                mask = np.abs(dq) < self._joint_deadband
                q_cmd[mask] = self._joint_q_last_cmd[mask]

            self._joint_q_last_cmd = q_cmd.copy()
            self._joint_last_pub_t = now

            joint_state.position = q_cmd.tolist()
            joint_state.velocity = []
            joint_state.effort = []
            
            self.pub_joint_state.publish(joint_state)
            
            # [디버그] 발행된 관절 위치 출력 (1초에 한 번)
            self.get_logger().info(
                f"[DEBUG] Published JointState(filt): pos={[round(p, 3) for p in joint_state.position[:4]]}...",
                throttle_duration_sec=1.0
            )
        else:
            # [디버그] motor_state 길이가 12 미만인 경우 경고
            self.get_logger().warn(
                f"[WARN] motor_state 길이 부족: {len(msg.motor_state)} (최소 12 필요)",
                throttle_duration_sec=1.0
            )

    def odom_callback(self, msg: Odometry):
        """Odometry를 TF(odom -> base_link)로 변환해 발행합니다."""
        # 원본 odom도 로컬 토픽으로 재발행 (Isaac Sim 측 직접 구독용)
        self.pub_odom_relay.publish(msg)

        # [디버그] Odom 수신 확인 (3초에 한 번)
        pos = msg.pose.pose.position
        self.get_logger().info(
            f"[DEBUG] Odom 수신: x={pos.x:.3f}, y={pos.y:.3f}, z={pos.z:.3f}",
            throttle_duration_sec=3.0
        )

        # quaternion -> yaw
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # 초기값 설정
        if self._tf_x is None:
            self._tf_x = float(msg.pose.pose.position.x)
            self._tf_y = float(msg.pose.pose.position.y)
            self._tf_yaw = float(yaw)
            self._tf_z_fixed = float(msg.pose.pose.position.z)
        else:
            a = self._tf_alpha
            self._tf_x = (1.0 - a) * self._tf_x + a * float(msg.pose.pose.position.x)
            self._tf_y = (1.0 - a) * self._tf_y + a * float(msg.pose.pose.position.y)
            # yaw unwrap
            dyaw = math.atan2(math.sin(yaw - self._tf_yaw), math.cos(yaw - self._tf_yaw))
            self._tf_yaw = self._tf_yaw + a * dyaw

        # yaw-only quaternion (roll/pitch 제거)
        half = 0.5 * self._tf_yaw
        qz = math.sin(half)
        qw = math.cos(half)

        tf_msg = TFMessage()
        tf = TransformStamped()
        tf.header.stamp = msg.header.stamp
        tf.header.frame_id = msg.header.frame_id if msg.header.frame_id else "odom"
        tf.child_frame_id = msg.child_frame_id if msg.child_frame_id else "base_link"
        tf.transform.translation.x = self._tf_x
        tf.transform.translation.y = self._tf_y
        tf.transform.translation.z = self._tf_z_fixed
        tf.transform.rotation.x = 0.0
        tf.transform.rotation.y = 0.0
        tf.transform.rotation.z = qz
        tf.transform.rotation.w = qw
        tf_msg.transforms.append(tf)
        self.pub_tf.publish(tf_msg)

def main(args=None):
    check_environment()
    rclpy.init(args=args)
    node = Go2BridgeServer()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info("Bridge Server Stopped by User")
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
