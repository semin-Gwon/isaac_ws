# Go2 디지털 트윈

실제 Go2의 관절과 몸체 자세를 Isaac Sim에 반영합니다.
SLAM이나 RGB-D 동기화 노드를 실행하지 않아도 관절·자세를 표시할 수 있습니다.

## 두 터미널로 실행

**터미널 1 — 메시지 변환기**

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" bridge
```

**터미널 2 — Isaac Sim**

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" sim
```

각 명령은 Conda와 ROS 환경을 자동으로 설정합니다. 변환기의 수신 로그를 확인한 뒤 Sim을 실행하세요.
기본 구성은 `$HOME/anaconda3/envs/isaaclab`의 Python 3.11과 워크스페이스의 Python 3.11용 `unitree_go`입니다.
두 프로그램은 별도 프로세스로 실행되며 CycloneDDS 기반 ROS 2 토픽으로 통신합니다.
각 실행 터미널에서 `Ctrl+C`로 종료합니다.

설치가 준비되어 있는지는 다음 명령으로 먼저 확인할 수 있습니다.

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" check
```

`check`는 `LowState`, `JointState`, `Odometry`, `TFMessage`의 타입 지원과 CycloneDDS 라이브러리를 불러옵니다.
ROS 노드를 생성하거나 실제 로봇 연결·Isaac Sim 전체 실행을 검사하지는 않습니다.
설치가 없다면 [환경 준비 안내](../../docs/setup.md)를 확인하세요.

## Bash 파일의 역할

| 파일·명령 | 역할 |
| --- | --- |
| [run.sh](./run.sh) `bridge` | `ros2_bridge_server.py` 실행 |
| `run.sh sim` | `go2_digital_twin.py` 실행 |
| `run.sh camera` | `go2_visualize.py` 실행 |
| `run.sh check` | 라이브러리 로딩 검사 |
| [env_go2_visualize.sh](./env_go2_visualize.sh) | Conda 활성화, Python 3.11 검사, ROS·DDS 환경 설정 |

환경 파일은 시스템 ROS에서 남은 Python·공유 라이브러리 경로를 제거한 뒤 Sim의 ROS 경로로 교체합니다.
`run.sh`는 이를 source하고 Python 프로세스를 `exec`하므로 종료 코드와 신호가 해당 프로세스로 전달됩니다.
추가 인자도 그대로 전달합니다.

```bash
# 현재 터미널에 환경만 설정
source "$HOME/isaac_ws/go2_real/digital_twin/env_go2_visualize.sh"

# Sim에 추가 인자 전달
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" sim --headless

# 사용법
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" --help
```

| 설정 | 기본값 |
| --- | --- |
| `GO2_CONDA_ROOT` | `$HOME/anaconda3`; 다른 Conda 설치 위치를 지정할 때 사용 |
| Conda 환경 | `<GO2_CONDA_ROOT>/envs/isaaclab` |
| Unitree 패키지 | `<워크스페이스>/install/unitree_go/lib/python3.11/site-packages` |
| RMW / domain | `rmw_cyclonedds_cpp` / `0` |
| DDS 설정 | [`../../config/cyclonedds.xml`](../../config/cyclonedds.xml), 인터페이스 `eno1` |
| 로그 | `/tmp/ros_logs` |

Python 소스 내부의 `ros2_bridge_humble`, `urdf_path`는 아직 개발 PC의 절대 경로를 사용합니다.
Conda 설치 위치나 사용자가 바뀌면 해당 경로도 확인해야 합니다.
SLAM용 [`../../scripts/env_ros2.sh`](../../scripts/env_ros2.sh)는 별도 시스템 ROS 터미널에서 사용합니다.

## Python 파일과 입력

| 파일 | 역할 | 입력 |
| --- | --- | --- |
| [ros2_bridge_server.py](./ros2_bridge_server.py) | LowState → JointState 변환, Odom 릴레이·TF 발행 | `/lf/lowstate`, `/utlidar/robot_odom` |
| [go2_digital_twin.py](./go2_digital_twin.py) | 관절·몸체 자세 시각화 | `/joint_states`, Odometry |
| [go2_visualize.py](./go2_visualize.py) | LowState 직접 수신 및 카메라 스크린 | `/lf/lowstate`, `/utlidar/robot_odom`, RGB |
| [tools/go2_import_ply.py](./tools/go2_import_ply.py) | 저장된 PLY를 USD Points로 가져오기 | PLY 파일, 열려 있는 Isaac Sim stage |

브리지는 필터를 적용한 관절 위치를 `/joint_states`로 발행하고 원본 Odom을 `/my_go2/robot_odom`으로 릴레이합니다.
관절 순서는 FR → FL → RR → RL의 hip·thigh·calf이며 속도·토크는 전달하지 않습니다.
현재 Sim 스크립트는 `/utlidar/robot_odom`, `/my_go2/robot_odom`, `/uslam/localization/odom`을 함께 구독합니다.

`Published JointState(filt)`는 관절 발행, `[ODOM]`은 Odom 수신, `[POSE]`는 자세 적용 로그입니다.
이 로그는 관절·몸체 상태가 같은 시점으로 동기화되었다는 의미는 아닙니다.

## 카메라 및 PLY 표시

카메라 포함 모드는 LowState를 직접 구독하므로 별도 관절 변환기 없이 실행할 수 있습니다.

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" camera
```

영상 입력은 `/my_go2/color/image_raw` 또는 `/my_go2/color/image_raw_sync`입니다.
`/camera/color/image_raw`는 직접 구독하지 않습니다.
두 시각화 스크립트는 `/tmp/go2.usd`를 공유하므로 `sim`과 `camera`는 하나를 선택해 실행합니다.

PLY는 `tools/go2_import_ply.py`의 `ply_file_path`를 맞춘 뒤 Isaac Sim의 Script Editor에서 실행합니다.
열린 stage의 `/World/rtabmap_cloud`에 포인트를 추가합니다.
현재 파서는 little-endian binary PLY의 고정된 31-byte vertex 구조를 가정합니다.

## 현재 확인된 제약

실행기는 환경 설정을 자동화하며 기존 관절·자세 처리 로직을 사용합니다.

| 영역 | 남아 있는 제약 |
| --- | --- |
| 입력 검증 | NaN·무한대·잘못된 quaternion 검증 부족. NaN이 관절 필터에 남을 수 있음 |
| 수신 상태 | 초기 관절 수신 전에도 0을 적용하고, 수신이 끊기면 마지막 자세를 계속 적용 |
| Odom | 여러 소스가 같은 상태를 갱신. 좌표계·시간 순서 검증 부족 |
| 추종 지연 | 관절 필터와 몸체 Odom의 시점이 맞지 않을 수 있음. 120Hz는 발행 상한 |
| 스레드·모델 | 상태 배열 공유에 잠금이 없고, URDF 관절 누락 시 위치값·인덱스 길이가 달라짐 |
| 이미지 | 행 패딩을 처리하지 않음. Pillow가 없는 경우 OpenCV의 `.jpg.tmp` 저장 오류 |
| PLY | 손상된 헤더의 EOF 반복, 고정된 필드 구조 가정 |

현재 PC에서 메시지·라이브러리 로딩과 `sim --help`를 확인했습니다.
실제 Sim 화면과 로봇 움직임의 전체 동기화 검증은 별도로 필요합니다.

전체 구성은 [루트 README](../../README.md), SLAM은 [`../slam/`](../slam/README.md),
과거 환경 구성은 [보관된 가이드](../../docs/archive/GO2_ISAACSIM_ROS2_GUIDE.md)에서 확인하세요.
