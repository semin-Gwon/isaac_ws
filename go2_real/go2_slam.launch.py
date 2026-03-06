import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, ExecuteProcess, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

_THIS_DIR = os.path.dirname(os.path.realpath(__file__))
_MAPS_DIR = os.path.join(_THIS_DIR, "maps")
_RTABMAP_DB_PATH = os.path.join(_MAPS_DIR, "rtabmap_real.db")

def generate_launch_description():
    os.makedirs(_MAPS_DIR, exist_ok=True)
    # 1) 런치 인자
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    use_viz = LaunchConfiguration('use_viz', default='false')

    # 2) 전역 시간/TF 동기화 노드
    # 참고: 로봇 측 타임스탬프를 현재 시간축에 맞춰 재발행하고 TF 트리 연결성을 유지한다.
    # 참고: bash -c에서 PYTHONPATH를 정리하고 ROS 2/워크스페이스 환경을 소싱한 뒤 실행한다.
    topic_sync = ExecuteProcess(
        cmd=['bash', '-c', 'mkdir -p /tmp/ros_logs; unset PYTHONPATH; source /opt/ros/humble/setup.bash; source /home/jnu/isaac_ws/install/local_setup.bash; /usr/bin/python3 /home/jnu/isaac_ws/go2_real/go2_topic_sync.py'],
        output='screen'
    )

    # 참고: Base Link -> Camera Optical Frame 연결은 topic_sync.py에서 처리됨.
    # 참고: Depth 이미지 재발행은 제거됨(실제 로봇은 Raw Depth 직접 전송).

    # 3) RTAB-Map SLAM 노드
    rtabmap_slam = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        respawn=True,
        respawn_delay=2.0,
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_depth': True,
            'subscribe_rgb': True,
            'approx_sync': True,             
            'approx_sync_max_interval': 0.3,
            'use_sim_time': use_sim_time,
            'queue_size': 50,
            'topic_queue_size': 20,
            'sync_queue_size': 50,
            'wait_for_transform': 1.0,
            'qos_image': 2,                  
            'qos_camera_info': 2,
            'Rtabmap/DetectionRate': '1.0',
            'Reg/Force3DoF': 'true',
            'database_path': _RTABMAP_DB_PATH,
        }],
        remappings=[
            ('rgb/image', '/my_go2/color/image_raw_sync'),
            ('depth/image', '/my_go2/depth/image_rect_raw_sync'),  # 동기화된 Raw Depth 직접 사용
            ('rgb/camera_info', '/my_go2/color/camera_info_sync'),  # 동기화/합성된 CameraInfo 사용
            ('odom', '/utlidar/robot_odom_sync')  # 동기화된 Odom 사용
        ]
    )

    # 4) RTAB-Map 시각화 노드 (GUI)
    rtabmap_viz = Node(
        package='rtabmap_viz',
        executable='rtabmap_viz',
        name='rtabmap_viz',
        output='screen',
        condition=IfCondition(use_viz),
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_depth': True,
            'subscribe_rgb': True,
            'subscribe_odom_info': False, 
            'approx_sync': True,
            'approx_sync_max_interval': 0.3,
            'use_sim_time': use_sim_time,
            'queue_size': 50,
            'topic_queue_size': 20,
            'sync_queue_size': 50,
            'wait_for_transform': 1.0,
            'qos_image': 2,
            'qos_camera_info': 2,
            'Reg/Force3DoF': 'false',
        }],
        remappings=[
            ('rgb/image', '/my_go2/color/image_raw_sync'),
            ('depth/image', '/my_go2/depth/image_rect_raw_sync'),  # 동기화된 Raw Depth 직접 사용
            ('rgb/camera_info', '/my_go2/color/camera_info_sync'),  # 동기화/합성된 CameraInfo 사용
            ('odom', '/utlidar/robot_odom_sync')  # 동기화된 Odom 사용
        ]
    )

    # 5) 런치 구성 반환
    return LaunchDescription([
        DeclareLaunchArgument('use_viz', default_value='false'),
        SetEnvironmentVariable(name='ROS_LOG_DIR', value='/tmp/ros_logs'),
        LogInfo(msg="Go2 비주얼 SLAM 시작 (토픽 동기화 포함, 실제 로봇 모드)..."),
        topic_sync,
        rtabmap_slam,
        rtabmap_viz
    ])
