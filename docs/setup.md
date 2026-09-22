# 환경 준비

## 설치 조건

```bash
git clone https://github.com/semin-Gwon/isaac_ws.git "$HOME/isaac_ws"
```

| 항목 | 현재 구성 / 준비 사항 |
| --- | --- |
| 운영체제 | Ubuntu 22.04 |
| 시스템 ROS | ROS 2 Humble, Python 3.10; SLAM·ROS CLI용 |
| 디지털 트윈 | `$HOME/anaconda3/envs/isaaclab`, Python 3.11 |
| 시뮬레이터 | 로컬 확인 버전: Isaac Sim `5.1.0.0`, Isaac Lab Python 패키지 `0.48.5` |
| GPU | Isaac Sim과 호환되는 NVIDIA GPU·드라이버 |
| Unitree 메시지 | `src/unitree_go`를 Python 3.11용으로 빌드한 `install/unitree_go` |
| Go2 모델 | 외부 URDF와 URDF가 참조하는 메시(mesh) 파일 |
| 로봇·센서 | Go2 연결 및 필요한 카메라·LiDAR 드라이버 별도 준비 |

실행기는 설치나 빌드를 자동으로 수행하지 않습니다.
`build/`와 `install/`은 Git에서 제외되므로 새로 복제한 환경에는 포함되지 않습니다.

`src/unitree_go`에는 로봇 상태·센서 메시지 26종, `src/unitree_api`에는 API 메시지 8종이 있습니다.
빌드에는 `colcon`, `ament_cmake`, `rosidl_default_generators`, `rosidl_generator_dds_idl`이 필요합니다.
현재 디지털 트윈 실행기가 읽는 Python 패키지 위치는 다음과 같습니다.

```text
install/unitree_go/lib/python3.11/site-packages/unitree_go/
```

**Python 3.10용 메시지 빌드는 이 실행기에서 사용할 수 없습니다.**
Python 버전을 바꿔 빌드할 때는 기존 빌드 캐시·설치 결과를 구분해야 합니다.
현재 PC에는 Python 3.11용 결과가 준비되어 있으므로 바로 `run.sh check`로 확인할 수 있습니다.

ROS 설치는 [ROS 2 Humble 안내](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html),
Sim의 ROS 라이브러리는 [Isaac Sim 5.1 ROS 설치 안내](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_ros.html)를 참고하세요.

## 경로와 네트워크

| 파일 | 역할 |
| --- | --- |
| [`go2_real/digital_twin/env_go2_visualize.sh`](../go2_real/digital_twin/env_go2_visualize.sh) | `isaaclab` 활성화, Python 3.11 검사, 시스템 ROS 경로 제거 후 Sim 라이브러리 지정 |
| [`scripts/env_ros2.sh`](../scripts/env_ros2.sh) | 시스템 ROS와 워크스페이스 환경 로드; SLAM·ROS CLI용 |
| [`config/cyclonedds.xml`](../config/cyclonedds.xml) | Go2와 통신할 네트워크 인터페이스 지정 |

두 환경 파일은 `source`로 사용합니다. 디지털 트윈 실행기는 자신의 환경 파일을 자동으로 불러옵니다.
ROS 설정은 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `ROS_DOMAIN_ID=0`,
`ROS_LOCALHOST_ONLY=0`, 로그 경로는 `/tmp/ros_logs`입니다.

현재 DDS 인터페이스는 `eno1`입니다. `ip -br address`로 실제 연결 인터페이스를 확인하고 XML을 맞추세요.
다른 터미널이나 외부 센서 노드도 같은 domain을 사용해야 합니다.

Conda의 설치 위치가 다르면 `GO2_CONDA_ROOT`를 지정할 수 있습니다.
다만 Python 소스 안의 아래 경로는 별도로 확인해야 합니다.

| 위치 | 현재 기본값 |
| --- | --- |
| 두 시각화 스크립트의 `ros2_bridge_humble` | `/home/jnu/anaconda3/envs/isaaclab/lib/python3.11/site-packages/isaacsim/exts/isaacsim.ros2.bridge/humble/rclpy` |
| 두 시각화 스크립트의 `urdf_path` | `/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf` |
| `tools/go2_import_ply.py`의 입력 | `/home/jnu/.ros/rtabmap_cloud.ply` |

## 환경 확인

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" check
```

이 명령은 메시지·CycloneDDS 라이브러리 로딩을 확인합니다.
실제 로봇 연결이나 Isaac Sim 전체 실행을 검사하는 명령은 아닙니다.

실제 뎁스·LiDAR 연결은 선택적으로 다음 명령으로 검사합니다.

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" sensors --seconds 10
```

카메라 모드는 `run.sh camera` 한 개로 실행하며, Unitree `LowState`를 직접 읽습니다.
기본 관절 모드는 별도 터미널의 `run.sh bridge`와 `run.sh sim`을 함께 사용합니다.
`sensors`는 연결 검사 도구이며 변환기를 대신하지 않습니다.
로컬에서 카메라 화면·센서 수신을 확인했으며, 시간 동기화 정확도와 SLAM 품질 검증은 별도로 필요합니다.

## 생성 파일

`build/`, `install/`, 로그, `outputs/`, 생성된 에셋·체크포인트, Python 캐시, 지도 DB·PLY/PCD,
ROS bag은 [`.gitignore`](../.gitignore)로 제외합니다. 새로 복제한 환경에서는 별도 준비가 필요합니다.

지도 DB는 `go2_real/slam/maps/`에 보관합니다.
기본 모드는 `rtabmap_real.db`, 외부 LIO 모드는 `rtabmap_real_lio.db`를 사용합니다.

[처음으로](../README.md) · [디지털 트윈 상세](../go2_real/digital_twin/README.md) · [SLAM 상세](../go2_real/slam/README.md)
