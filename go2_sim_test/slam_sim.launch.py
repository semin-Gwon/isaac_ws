from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    # 1. 설정 가능한 인자
    # 시뮬레이션 환경이므로 use_sim_time의 기본값을 true로 설정합니다.
    use_sim_time = LaunchConfiguration('use_sim_time', default='true')
    
    # 2. RTAB-Map SLAM 노드
    rtabmap_slam = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_depth': True,         # [FIX] Depth 발행됨
            'subscribe_rgb': True,
            'subscribe_scan': False,
            'approx_sync': True,             # [FIX] 시뮬레이션 지연 고려하여 허용
            'use_sim_time': False,
            'queue_size': 10,                
            'qos_image': 2,                  
            'qos_camera_info': 2,
            'publish_tf': True,              # SLAM에서 map -> odom TF 발행 활성화
            'Mem/IncrementalMemory': 'true', # RGB 전용 모드
            'Mem/InitWMWithAllNodes': 'false',
        }],
        remappings=[
            # [FIX] Isaac Sim이 실제로 발행하는 토픽으로 수정
            ('rgb/image', '/camera/image_raw'),
            ('rgb/camera_info', '/camera/camera_info'),
            ('depth/image', '/camera/depth/image_raw'), # [NEW] Depth 추가
            ('odom', '/odom')  # [FIX] /utlidar/robot_odom -> /odom
        ],
        arguments=['--delete_db_on_start'] 
    )

    # 3. RTAB-Map 시각화 노드 (GUI)
    rtabmap_viz = Node(
        package='rtabmap_viz',
        executable='rtabmap_viz',
        name='rtabmap_viz',
        output='screen',
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_depth': True,         # [FIX] Depth 발행됨
            'subscribe_rgb': True,
            'subscribe_scan': False,
            'subscribe_odom_info': False, 
            'approx_sync': True,             # [FIX] 시뮬레이션 지연 고려하여 허용
            'use_sim_time': use_sim_time,
            'queue_size': 10,
            'qos_image': 2,
            'qos_camera_info': 2,
        }],
        remappings=[
            # [FIX] Isaac Sim이 실제로 발행하는 토픽으로 수정
            ('rgb/image', '/camera/image_raw'),
            ('rgb/camera_info', '/camera/camera_info'),
            ('depth/image', '/camera/depth/image_raw'), # [NEW] Depth 추가
            ('odom', '/odom')  # [FIX] /utlidar/robot_odom -> /odom
        ]
    )

    return LaunchDescription([
        LogInfo(msg="Isaac Sim 전용 Go2 비주얼 SLAM 시작 (Simulation Mode)..."),
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='true',
            description='Use simulation (Gazebo/IsaacSim) clock if true'),
        rtabmap_slam,
        rtabmap_viz
    ])
