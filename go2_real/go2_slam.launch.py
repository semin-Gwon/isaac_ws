from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, ExecuteProcess, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import sys

def generate_launch_description():
    # 1. 설정 가능한 인자
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    use_viz = LaunchConfiguration('use_viz', default='false')

    # 2. 이미지 압축 해제 노드 (Isaac Sim은 압축된 이미지를 전송 -> RTAB-Map은 Raw 이미지가 필요)
    
    # RGB 이미지 재발행
    # 입력: /my_go2/color/image_raw/compressed
    # 출력: /camera/rgb/image_raw
    # 4. 전역 시간/TF 동기화 노드
    # 로봇의 데이터(2022 타임스탬프)를 가로채 현재 시간(2026)으로 재발행
    # bash -c를 사용하여 환경을 정리(PYTHONPATH 해제)하고 시스템 ROS 2를 소싱하여 rclpy/tf2_msgs를 찾도록 함
    topic_sync = ExecuteProcess(
        cmd=['bash', '-c', 'mkdir -p /tmp/ros_logs; export ROS_LOG_DIR=/tmp/ros_logs; export ROS_LOCALHOST_ONLY=0; export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; export ROS_DOMAIN_ID=0; export CYCLONEDDS_URI=file:///home/jnu/cyclonedds.xml; unset PYTHONPATH; source /opt/ros/humble/setup.bash; source /home/jnu/isaac_ws/install/local_setup.bash; /usr/bin/python3 /home/jnu/isaac_ws/go2_real/go2_topic_sync.py'],
        output='screen'
    )

    # 4.2 Base Link -> Camera Optical Frame
    # 단일 TF 트리 연결성을 보장하기 위해 이제 topic_sync.py에서 처리됨


    
    # Depth 이미지 재발행 제거됨 (실제 로봇은 Raw Depth 전송)

    # 3. RTAB-Map SLAM 노드
    rtabmap_slam = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_depth': True,
            'subscribe_rgb': True,
            'approx_sync': True,             
            'approx_sync_max_interval': 0.3,
            'use_sim_time': use_sim_time,
            'queue_size': 200,
            'topic_queue_size': 100,
            'sync_queue_size': 200,
            'wait_for_transform': 1.0,
            'qos_image': 2,                  
            'qos_camera_info': 2,
            'Rtabmap/DetectionRate': '2.0',
        }],
        remappings=[
            ('rgb/image', '/my_go2/color/image_raw_sync'),
            ('depth/image', '/my_go2/depth/image_rect_raw_sync'), # 동기화된 Raw Depth 직접 사용
            ('rgb/camera_info', '/my_go2/color/camera_info_sync'), # 동기화/합성된 정보 사용
            ('odom', '/utlidar/robot_odom_sync')                 # 동기화된 Odom 사용
        ],
        arguments=['--delete_db_on_start'] 
    )

    # 4. RTAB-Map 시각화 노드 (GUI)
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
            'queue_size': 200,
            'topic_queue_size': 100,
            'sync_queue_size': 200,
            'wait_for_transform': 1.0,
            'qos_image': 2,
            'qos_camera_info': 2,
        }],
        remappings=[
            ('rgb/image', '/my_go2/color/image_raw_sync'),
            ('depth/image', '/my_go2/depth/image_rect_raw_sync'), # 동기화된 Raw Depth 직접 사용
            ('rgb/camera_info', '/my_go2/color/camera_info_sync'), # 동기화/합성된 정보 사용
            ('odom', '/utlidar/robot_odom_sync')                 # 동기화된 Odom 사용
        ]
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_viz', default_value='false'),
        SetEnvironmentVariable(name='RMW_IMPLEMENTATION', value='rmw_cyclonedds_cpp'),
        SetEnvironmentVariable(name='ROS_DOMAIN_ID', value='0'),
        SetEnvironmentVariable(name='ROS_LOCALHOST_ONLY', value='0'),
        SetEnvironmentVariable(name='CYCLONEDDS_URI', value='file:///home/jnu/cyclonedds.xml'),
        SetEnvironmentVariable(name='ROS_LOG_DIR', value='/tmp/ros_logs'),
        LogInfo(msg="Go2 비주얼 SLAM 시작 (토픽 동기화 포함, 실제 로봇 모드)..."),
        topic_sync,

        # depth_republish, # Removed
        rtabmap_slam,
        rtabmap_viz
    ])
