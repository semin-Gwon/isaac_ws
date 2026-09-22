# Go2 디지털 트윈

실제 Go2의 관절·몸체 자세와 RGB 영상을 Isaac Sim에 표시합니다.
뎁스·LiDAR는 수신 및 진단 로그를 지원합니다. SLAM은 별도로 실행합니다.

## 실행 모드 선택

| 모드 | 관절 입력 | 별도 변환기 | 화면 |
| --- | --- | --- | --- |
| `camera` | `unitree_go/msg/LowState` 직접 해석 | 필요 없음 | 관절·자세 + RGB |
| `sim` | `sensor_msgs/msg/JointState` | `bridge` 필요 | 관절·자세 |

`camera`는 Python 3.11용 Unitree 메시지 라이브러리를 불러와 관절 각도를 직접 읽습니다.
`bridge`가 발행하는 `/joint_states`는 구독하지 않습니다.
두 모드는 `/tmp/go2.usd`를 공유하므로 하나만 실행하세요. 종료는 `Ctrl+C`입니다.

### 카메라 포함 — 터미널 한 개

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" camera
```

스크린은 로봇 앞에서 로봇 쪽을 향하며, 전방을 바라보는 로봇 시점에서 영상의 좌우가 맞습니다.
`[DIAG]`의 RGB 수신 횟수와 `texture_updates`가 증가하면 영상 수신과 텍스처 갱신이 진행 중입니다.
메모리 텍스처를 사용해 JPEG 파일 반복 교체로 발생하던 에셋 로더 크래시를 피하고, 발광 재질로 영상을 표시합니다.

<details>
<summary>실제 실행 화면 보기</summary>

![로봇 뒤에서 전방을 바라본 RGB 스크린](../../docs/images/go2-camera-view.png)

2026-09-22 로컬 실행 화면. 뎁스 영상과 실시간 LiDAR 포인트 렌더링은 아직 구현하지 않았습니다.

</details>

### 관절·자세만 — 터미널 두 개

**터미널 1 — 변환기**

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" bridge
```

**터미널 2 — Isaac Sim**

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" sim
```

변환기는 `/lf/lowstate`를 `/joint_states`로 변환하고, Odom을 `/my_go2/robot_odom`으로 재발행합니다.
관절은 FR → FL → RR → RL의 hip·thigh·calf 순서이며, 필터링한 각도만 전달합니다.
속도·토크는 제외하고, Odom으로부터 보정된 `/tf`도 발행합니다.

## 카메라 모드의 입력과 진단

| 데이터 | 기본 토픽 | 메시지 타입 | 처리 |
| --- | --- | --- | --- |
| 관절 | `/lf/lowstate` | `unitree_go/msg/LowState` | 12개 관절 각도 적용 |
| 몸체 자세 | `/utlidar/robot_odom` | `nav_msgs/msg/Odometry` | 위치·방향 적용 |
| RGB | `/camera/color/image_raw` | `sensor_msgs/msg/Image` | 스크린 표시 |
| 뎁스 | `/camera/aligned_depth_to_color/image_raw` | `sensor_msgs/msg/Image` | 미터 단위 배열·진단 |
| LiDAR | `/utlidar/cloud_base` | `sensor_msgs/msg/PointCloud2` | XYZ 배열·진단, 기본 좌표계 `base_link` |

카메라·로봇의 토픽 발행은 미리 준비되어 있어야 합니다. 실행기가 센서 드라이버를 시작하지는 않습니다.
다른 입력은 다음 옵션으로 선택합니다.

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" camera \
  --camera-topic /camera/color/image_raw \
  --depth-topic /camera/depth/image_rect_raw \
  --lidar-topic /utlidar/cloud
```

뎁스는 `16UC1`(mm)·`32FC1`(m)을 지원하고 유효하지 않은 값을 NaN으로 저장합니다.
LiDAR는 필드 위치·행 패딩·바이트 순서를 반영해 유효 XYZ를 읽습니다.
각 센서는 최신 배열과 원본 헤더를 유지하며, 좌표계 변환이나 RGB·Odom과의 시간 동기화는 수행하지 않습니다.

### 선택 사항: Sim 없이 센서 수신 검사

```bash
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" sensors --seconds 10
```

`sensors`는 뎁스·LiDAR 연결만 검사하고 자동 종료하는 도구입니다. 관절 브릿지 역할은 하지 않습니다.
`--depth-topic`, `--lidar-topic`도 사용할 수 있습니다.

`[DEPTH]`, `[LIDAR]`에는 수신 횟수·평균 Hz·마지막 수신 후 경과 시간·좌표계·깊이 범위/포인트 수가 나옵니다.
`OK`는 유효 데이터 수신, `WAITING`은 미수신, `STALE`은 2초 이상 수신 중단,
`EMPTY`는 유효 값 없음, `ERROR`는 해석 오류입니다.
두 센서가 `OK`이고 해석 오류가 없으면 `PASS`와 종료 코드 0, 그 외에는 `FAIL`과 1을 반환합니다.

## 환경과 파일

설치 조건과 경로 변경은 [환경 준비 안내](../../docs/setup.md)를 참고하세요.
모든 `run.sh` 명령이 Conda·ROS·DDS 환경을 자동 설정합니다.

| 설정 | 기본값 |
| --- | --- |
| Conda / Python | `$HOME/anaconda3/envs/isaaclab` / 3.11 |
| Unitree 메시지 | `install/unitree_go/lib/python3.11/site-packages` |
| RMW / domain | `rmw_cyclonedds_cpp` / `0` |
| DDS | [cyclonedds.xml](../../config/cyclonedds.xml), 인터페이스 `eno1` |
| ROS 로그 | `/tmp/ros_logs` |

환경 파일은 시스템 ROS의 Python·공유 라이브러리 경로를 제거하고 Sim의 ROS 경로를 설정합니다.
`GO2_CONDA_ROOT`로 Conda 위치를 지정할 수 있지만, Python 소스의 `ros2_bridge_humble`·`urdf_path`도 확인해야 합니다.
SLAM은 별도 시스템 ROS 환경인 [env_ros2.sh](../../scripts/env_ros2.sh)를 사용합니다.

```bash
# 설치된 메시지 타입 지원과 CycloneDDS 라이브러리 로딩 검사
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" check

# 전체 사용법
bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" --help
```

`check`는 로봇 연결이나 Sim 실행을 검사하지 않습니다.
환경만 설정하려면 `source "$HOME/isaac_ws/go2_real/digital_twin/env_go2_visualize.sh"`를 사용합니다.
추가 인자는 Python에 전달되므로 `run.sh sim --headless`, `run.sh camera --help`처럼 사용할 수 있습니다.

| 파일 | 역할 |
| --- | --- |
| [run.sh](./run.sh), [env_go2_visualize.sh](./env_go2_visualize.sh) | 실행 모드 선택·환경 설정 |
| [go2_visualize.py](./go2_visualize.py) | `camera`: 관절·자세·RGB 표시와 센서 구독 |
| [go2_digital_twin.py](./go2_digital_twin.py) | `sim`: JointState·Odom 수신 |
| [ros2_bridge_server.py](./ros2_bridge_server.py) | `bridge`: 관절 변환·Odom 릴레이·TF 발행 |
| [sensor_subscriptions.py](./sensor_subscriptions.py) | 뎁스·LiDAR 공통 수신 코드 및 `sensors` 검사 |
| [tools/go2_import_ply.py](./tools/go2_import_ply.py) | 저장된 PLY를 Sim stage의 USD Points로 가져오기 |

PLY 표시는 `ply_file_path`를 맞춘 뒤 Isaac Sim의 Script Editor에서 실행합니다.
현재 파서는 little-endian binary PLY의 고정된 31-byte vertex 구조를 가정합니다.

## 검증과 남아 있는 제약

2026-09-22 로컬 환경에서 RGB·뎁스·LiDAR의 동시 수신과 카메라 모드 60초 렌더링·정상 종료를 확인했습니다.
스크린 방향 수정 후에는 20초간 재실행해 로봇 시점과 영상 좌우 방향을 확인했습니다.
데이터 해석·수신 상태·텍스처 업로드 관련 테스트 11개를 실행할 수 있습니다.

```bash
source "$HOME/isaac_ws/go2_real/digital_twin/env_go2_visualize.sh"
"$GO2_PYTHON" -m unittest discover -s "$GO2_WS/go2_real/digital_twin/tests" -v
```

| 범위 | 남아 있는 제약 |
| --- | --- |
| 관절·자세 | NaN·무한대·잘못된 quaternion 검증 부족. 수신 전 0 적용·수신 중단 후 마지막 자세 유지 |
| `sim`의 Odom | 여러 소스가 같은 상태를 갱신하며 좌표계·시간 순서 검증 부족 |
| 브릿지 필터 | 관절과 몸체의 시점이 맞지 않을 수 있음. 관절 120Hz는 발행 상한 |
| 모델·스레드 | 관절 누락 시 값·인덱스 길이 불일치. `sim`의 공유 상태 잠금 없음 |
| RGB | 행 패딩 미지원 |
| 뎁스·LiDAR | 수신·진단까지만 구현. Sim 렌더링·TF 변환·센서 간 시간 동기화 미구현 |
| PLY | 손상된 헤더의 EOF 반복, 고정된 필드 구조 가정 |

참고: [메모리 텍스처 API](https://docs.omniverse.nvidia.com/kit/docs/omni.ui/3.2.0/omni.ui/omni.ui.DynamicTextureProvider.html),
[깊이 영상 규약 REP-118](https://github.com/ros-infrastructure/rep/blob/master/rep-0118.rst).

[루트 README](../../README.md) · [SLAM](../slam/README.md) · [과거 구성 기록](../../docs/archive/GO2_ISAACSIM_ROS2_GUIDE.md)
