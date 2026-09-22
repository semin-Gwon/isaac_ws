# Go2 SLAM

센서 동기화, RTAB-Map 실행, RViz 표시와 로컬 지도 DB를 모았습니다.
Isaac Sim 디지털 트윈은 [`../digital_twin/`](../digital_twin/README.md)에 있으며, SLAM과 별도로 실행합니다.

| 파일 | 역할 |
| --- | --- |
| [go2_topic_sync.py](./go2_topic_sync.py) | RGB·Depth·LiDAR·Odometry 매칭, 동기화 토픽·TF 발행 |
| [go2_slam.launch.py](./go2_slam.launch.py) | 동기화 노드와 RGB-D 기반 RTAB-Map 실행, LiDAR 선택 |
| [go2_slam_lio.launch.py](./go2_slam_lio.launch.py) | 외부 `*_synced` LiDAR·Odom을 입력받는 RTAB-Map 설정 |
| [go2_sim.rviz](./go2_sim.rviz) | 지도·이미지·TF 확인용 RViz 설정 |
| `maps/` | 로컬 지도 DB 및 내보낸 PLY/PCD 보관, Git 제외 |

## 환경 준비

SLAM은 시스템 ROS 2 Humble / Python 3.10 터미널을 사용합니다.
디지털 트윈의 `run.sh`와는 별도 환경입니다.
`rtabmap_ros`, `rviz2`, CycloneDDS, NumPy, OpenCV와 센서 드라이버가 필요합니다.
공통 설정은 [`../../scripts/env_ros2.sh`](../../scripts/env_ros2.sh), DDS 인터페이스는
[`../../config/cyclonedds.xml`](../../config/cyclonedds.xml)에서 확인합니다.

ROS 2 Humble과 ROS apt 저장소가 이미 설치된 Ubuntu 22.04 환경의 의존성 설치 예입니다.

```bash
sudo apt update
sudo apt install \
  ros-humble-rtabmap-ros ros-humble-rviz2 ros-humble-rmw-cyclonedds-cpp \
  python3-numpy python3-opencv \
  ros-humble-sensor-msgs ros-humble-nav-msgs ros-humble-tf2-ros ros-humble-tf2-msgs
```

환경 스크립트는 기존 `install/local_setup.bash`도 불러옵니다.
현재 동기화 노드는 표준 ROS 메시지를 사용합니다. Python 3.11용 Unitree 메시지를
시스템 Python 3.10 노드에서 직접 가져오는 것은 지원되지 않습니다.

## 실행 모드

입력 센서 노드는 별도로 실행되어 있어야 합니다. 사용할 센서 구성에 따라 아래 모드 중 하나를 선택합니다.
두 SLAM launch는 같은 `/rtabmap` 이름과 지도 토픽을 사용하므로 한 번에 하나씩 실행합니다.

### RGB-D + Odometry

LiDAR 없이 RGB-D 입력을 먼저 확인할 때 사용합니다.
launch는 IMU 구독도 켜므로, 연결된 IMU 토픽과 TF도 함께 확인하세요.

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch "$HOME/isaac_ws/go2_real/slam/go2_slam.launch.py" \
  use_lidar:=false use_viz:=true
```

`topic_sync`, `point_cloud_xyzrgb`, RTAB-Map과 선택한 RTAB-Map Viz가 실행됩니다.
`topic_sync`를 별도로 실행할 필요는 없습니다.

### RGB-D + LiDAR + Odometry

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch "$HOME/isaac_ws/go2_real/slam/go2_slam.launch.py" \
  use_lidar:=true use_viz:=true
```

`use_lidar`의 기본값은 **`true`**입니다.
이 모드에서는 시간 허용 범위 안의 LiDAR 메시지가 있어야 RGB-D 묶음도 발행됩니다.
RTAB-Map에는 RGB-D와 scan cloud가 함께 전달되며, 현재 `Reg/Strategy`는 `0`으로 설정되어 있습니다.

| `go2_slam.launch.py` 인자 | 기본값 | 동작 |
| --- | --- | --- |
| `use_lidar` | `true` | 동기화 묶음에 LiDAR를 요구하고 RTAB-Map의 scan cloud 구독 활성화 |
| `use_viz` | `false` | RTAB-Map Viz 실행 |
| `odom_eval_mode` | `false` | `true`이면 동기화 노드의 `odom → base_link` TF 발행을 생략; 외부 TF 필요 |

`use_sim_time`은 코드 내부에서 기본 `false`로 참조하지만, 이 launch의 선언된 인자 목록에는 없습니다.
실제 로봇의 시간을 사용하는 구성을 기준으로 합니다.

### 외부 LIO 입력을 사용하는 LiDAR 매핑

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch "$HOME/isaac_ws/go2_real/slam/go2_slam_lio.launch.py" \
  use_lidar:=true use_viz:=true
```

이 launch는 RTAB-Map과 선택한 Viz만 실행합니다.
LIO 추정기나 동기화 노드를 시작하지 않으며, RGB-D 입력도 구독하지 않습니다.
`Reg/Strategy=1`의 ICP 등록과 `Reg/Force3DoF=true` 설정을 사용합니다.

외부 파이프라인에서 다음을 준비합니다.

- `/utlidar/cloud_deskewed_synced` — `sensor_msgs/msg/PointCloud2`
- `/utlidar/robot_odom_synced` — `nav_msgs/msg/Odometry`
- `odom → base_link`와 LiDAR 프레임을 연결하는 TF

`/utlidar/imu_synced` 리매핑도 있지만 현재 `subscribe_imu=False`입니다.
이 모드의 **`_synced`는 기본 동기화 노드가 발행하는 `_sync`와 다른 토픽**입니다.

선언된 인자는 `use_sim_time=false`, `use_viz=false`, `use_lidar=true`입니다.
여기서 `use_lidar=false`로 설정하면 RGB-D 모드로 바뀌는 것이 아니라 SLAM 노드가 실행되지 않습니다.

### RViz로 결과 확인

별도 시스템 ROS 터미널에서 실행합니다.

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
rviz2 -d "$HOME/isaac_ws/go2_real/slam/go2_sim.rviz"
```

기본 Fixed Frame은 `map`입니다. `/map`, `/cloud_map`, `/rgbd_cloud`, 동기화한 RGB·Depth·Odometry를 표시합니다.
`LiDAR Cloud Sync` 디스플레이는 기본 비활성화되어 있습니다.
LIO 모드에서는 RGB-D 디스플레이를 끄고, LiDAR·Odometry 디스플레이의 토픽을 해당 모드의 입력에 맞춥니다.

## 토픽과 동기화

아래는 [`go2_topic_sync.py`](./go2_topic_sync.py)의 입력과 출력입니다.
여러 입력 이름은 구독 후보이며, 동일 센서의 여러 토픽이 동시에 발행되면 같은 버퍼에 들어갑니다.

| 데이터 | 입력 토픽 | 출력 토픽 |
| --- | --- | --- |
| RGB raw | `/my_go2/color/image_raw`, `/camera/color/image_raw` | `/my_go2/color/image_raw_sync` |
| RGB compressed | 위 두 RGB 토픽의 `/compressed` | 같은 RGB raw 출력으로 디코딩 |
| Depth raw | `/my_go2/depth/image_rect_raw`, `/camera/depth/image_rect_raw` | `/my_go2/depth/image_rect_raw_sync` |
| CameraInfo | `/my_go2/color/camera_info`, `/camera/color/camera_info`, `/camera/camera_info` | `/my_go2/color/camera_info_sync` |
| Odometry | `/utlidar/robot_odom`, `/my_go2/robot_odom`, `/uslam/localization/odom`, `/uslam/frontend/odom` | `/utlidar/robot_odom_sync` |
| LiDAR | `/utlidar/cloud`, `/utlidar/cloud_deskewed`, `/utlidar/cloud_base` | `/utlidar/cloud_sync` |
| IMU | `/utlidar/imu`, `/imu/data`, `/my_go2/imu` | `/utlidar/imu_sync` |

RGB가 도착하면 버퍼의 가장 가까운 Depth·LiDAR·Odometry를 찾습니다.
Depth는 **80 ms**, LiDAR는 **150 ms**, Odometry는 **100 ms**를 기준으로 매칭합니다.
Odometry가 100 ms 안에 없으면 버퍼에 남아 있는 최신 메시지를 사용하므로, 이는 엄격한 시간 차이 상한이 아닙니다.
버퍼의 시간 유지 범위는 2초이며 메시지 개수도 제한합니다.

유효한 묶음의 RGB·Depth·CameraInfo·LiDAR·Odometry는 선택한 Odometry 시각을 공유합니다.
IMU는 별도 콜백에서 재발행하며 이 묶음의 매칭 대상에 포함되지 않습니다.
동기화된 Odometry도 묶음 발행 시 나가기 때문에 RGB-D 입력이 없으면 출력이 멈출 수 있습니다.

현재 카메라 좌표계는 다음과 같습니다.

```text
map → odom → base_link → camera_link → camera_optical_frame
```

`map → odom`은 RTAB-Map, `odom → base_link`는 기본 모드의 동기화 노드가 담당합니다.
카메라 변환은 코드에 고정되어 있으며 `base_link → camera_link`의 이동량은 `(0.3, 0.0, 0.1) m`입니다.
실제 장착 위치와 캘리브레이션에 맞게 조정하세요.

CameraInfo 입력을 저장하며, 입력이 없으면 640×480 기준의 근사 내부 파라미터를 사용합니다.
RGB 콜백에서 내부 파라미터가 다시 계산되는 경로가 있어 실제 보정값 보존 여부를 확인해야 합니다.
Depth는 raw `Image` 입력만 구독합니다. `passthrough` 인코딩은 `step`을 보고 `16UC1` 또는 `32FC1`로 바꾸지만,
영상 정합이나 깊이 값의 단위 변환까지 수행하지는 않습니다.

## 지도 저장

두 launch 파일은 자신과 같은 폴더에 `maps/`를 생성합니다.

| 실행 모드 | 파일 |
| --- | --- |
| RGB-D / RGB-D + LiDAR | `maps/rtabmap_real.db` |
| 외부 LIO 입력 | `maps/rtabmap_real_lio.db` |

RGB-D 모드와 LiDAR를 추가한 모드는 같은 DB를 사용합니다.
기존 DB는 보존되어 있으며 Git에서 제외합니다. 실험별 보관이 필요하면 RTAB-Map 종료 후 백업하세요.

## 상태 확인

별도 시스템 ROS 터미널에서 실행합니다. `hz`와 `tf2_echo`는 각 명령 확인 후 `Ctrl+C`로 종료합니다.

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 node info /rtabmap
ros2 topic hz /my_go2/color/image_raw_sync
ros2 topic hz /utlidar/robot_odom_sync
ros2 run tf2_ros tf2_echo map base_link
```

## 현재 확인된 제약

센서 간 시각 기준 차이, 여러 LiDAR 입력과 좌표계 혼용, RGB·Depth 정렬 및 매칭 문제는 별도 수정이 필요합니다.
외부 LIO launch가 요구하는 `*_synced` 토픽은 이 저장소에서 생성하지 않습니다.
브리지나 외부 추정기를 함께 실행할 때는 같은 TF의 중복 발행도 확인해야 합니다.

과거 통합 과정은 [보관된 가이드](../../docs/archive/GO2_RTABMAP_GUIDE.md)를 참고하세요.
전체 구조와 디지털 트윈 실행은 [루트 README](../../README.md)에서 확인할 수 있습니다.
