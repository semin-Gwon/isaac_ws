# 환경 설정

## Quick Start · 설치된 환경 검사

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" check
```

- 검사: Python·메시지 타입·CycloneDDS 라이브러리
- 제외: 실제 로봇 수신·Sim 실행
- 실행 명령: [디지털 트윈](../go2_real/digital_twin/README.md) · [SLAM](../go2_real/slam/README.md)

## 설치 조건

| 항목 | 구성 |
| --- | --- |
| OS | Ubuntu 22.04 |
| SLAM·ROS CLI | ROS 2 Humble · Python 3.10 |
| 디지털 트윈 | Conda `isaaclab` · Python 3.11 |
| 로컬 확인 버전 | Isaac Sim `5.1.0.0` · Isaac Lab `0.48.5` |
| GPU | Isaac Sim 호환 NVIDIA GPU·드라이버 |
| Unitree | Python 3.11용 `unitree_go` 빌드 |
| 모델·센서 | 외부 Go2 URDF·mesh / 센서 드라이버 별도 준비 |

### 저장소 복제 · 최초 설치

```bash
git clone https://github.com/semin-Gwon/isaac_ws.git "$HOME/isaac_ws"
```

- 자동 설치·빌드 기능 없음
- `build/`·`install/`: Git 제외 → 새 환경에서 별도 준비
- ROS 설치: [Humble](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)
- Sim ROS 설치: [Isaac Sim 5.1](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_ros.html)

### Unitree 메시지

- 소스: `src/unitree_go` · `src/unitree_api`
- 빌드 도구: `colcon` · `ament_cmake` · `rosidl_default_generators` · `rosidl_generator_dds_idl`
- 실행기 경로: `install/unitree_go/lib/python3.11/site-packages`
- Python 3.10 빌드: 디지털 트윈과 호환 불가
- 버전별 빌드 캐시·설치 결과 분리 필요

### SLAM 의존성

전제: Ubuntu 22.04 · ROS 2 Humble·ROS apt 저장소 설치 완료

```bash
sudo apt update
sudo apt install \
  ros-humble-rtabmap-ros ros-humble-rviz2 ros-humble-rmw-cyclonedds-cpp \
  python3-numpy python3-opencv \
  ros-humble-sensor-msgs ros-humble-nav-msgs ros-humble-tf2-ros ros-humble-tf2-msgs
```

## 환경·네트워크

| 설정 | 값 |
| --- | --- |
| 디지털 트윈 환경 | [env_go2_visualize.sh](../go2_real/digital_twin/env_go2_visualize.sh) · `run.sh`에서 자동 적용 |
| SLAM 환경 | [env_ros2.sh](../scripts/env_ros2.sh) · `source`로 적용 |
| RMW / Domain | `rmw_cyclonedds_cpp` / `0` |
| DDS | [cyclonedds.xml](../config/cyclonedds.xml) · 현재 `eno1` |
| ROS 로그 | `/tmp/ros_logs` |
| Conda 루트 | `$HOME/anaconda3` · 변경: `GO2_CONDA_ROOT` |

- 네트워크 확인: `ip -br address`
- 로봇·센서·PC: 동일 ROS domain
- SLAM: 시스템 Python 3.10 / 디지털 트윈: Python 3.11
- SLAM 환경에서 Python 3.11용 Unitree 메시지 직접 사용 불가

### PC 변경 시 확인할 경로

| 위치 | 현재 값 |
| --- | --- |
| 시각화 코드의 `ros2_bridge_humble` | `/home/jnu/anaconda3/envs/isaaclab/lib/python3.11/site-packages/isaacsim/exts/isaacsim.ros2.bridge/humble/rclpy` |
| 시각화 코드의 `urdf_path` | `/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf` |
| PLY 도구의 `ply_file_path` | `/home/jnu/.ros/rtabmap_cloud.ply` |

## 생성 파일

- [Git 제외](../.gitignore): 빌드·설치 결과·로그·캐시·에셋·체크포인트·ROS bag·지도
- 지도 위치·DB 이름: [SLAM 지도 저장](../go2_real/slam/README.md#지도-저장)

[메인](../README.md) · [디지털 트윈](../go2_real/digital_twin/README.md) · [SLAM](../go2_real/slam/README.md)
