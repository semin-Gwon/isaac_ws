# Go2 SLAM

## Quick Start

- 처음 사용: [설치 안내](../../docs/setup.md)의 1·3·5·6단계 · SLAM 전용 빌드 선택
- 필수: ROS 2 Humble·Python 3.10 · RTAB-Map·RViz·NumPy·OpenCV
- 디지털 트윈과 함께 사용: 설치 안내 전체 단계 · Python 3.11 메시지 빌드 선택
- 입력: RGB·Depth·CameraInfo·Odom·IMU·LiDAR 발행 중
- 장착 위치별 카메라 보정·TF 확인 필요 · [좌표계](#동기화좌표계) / [현재 제약](#제약관련-파일)
- SLAM 모드 하나만 선택 · 종료: `Ctrl+C`

### 터미널 1 — RGB-D 카메라

**RealSense 예시** · 이미 카메라 토픽 발행 중이면 생략

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch realsense2_camera rs_launch.py \
  camera_namespace:=/ camera_name:=camera \
  enable_color:=true enable_depth:=true align_depth.enable:=true
```

### 터미널 2 — RGB-D + LiDAR SLAM

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch "$HOME/isaac_ws/go2_real/slam/go2_slam.launch.py" \
  use_lidar:=true use_viz:=true
```

- 실행: 동기화 노드 + RGB-D cloud + RTAB-Map + Viz
- 확인: 동기화 로그의 `sync_success` 증가 · `/rtabmap` 실행 · 지도 갱신
- RGB-D만 사용: `use_lidar:=false`
- LiDAR 활성화 시: 매칭 가능한 LiDAR 메시지 필수

### 터미널 3 — RViz · 선택

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
rviz2 -d "$HOME/isaac_ws/go2_real/slam/go2_sim.rviz"
```

- Fixed Frame: `map`
- 표시: 지도·RGB-D cloud·영상·TF
- LiDAR 디스플레이: 기본 비활성화

### 외부 LIO · 선택

**터미널 2의 기본 SLAM 대신 실행** · 외부 추정기·`*_synced` 입력이 준비된 경우

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch "$HOME/isaac_ws/go2_real/slam/go2_slam_lio.launch.py" \
  use_lidar:=true use_viz:=true
```

- 외부 LIO·동기화 노드: 별도 실행 필요
- 필수 입력: `/utlidar/cloud_deskewed_synced` · `/utlidar/robot_odom_synced`
- 필수 TF: `odom → base_link → LiDAR 프레임`
- `*_synced`: 외부 입력 / `*_sync`: 기본 동기화 노드 출력
- RGB-D·IMU 구독 없음 · ICP·3DoF 설정
- RViz: RGB-D 표시 해제·LiDAR/Odom 토픽 변경

## 실행 옵션

| 옵션 | 기본값 | 동작 |
| --- | --- | --- |
| `use_lidar` | `true` | 기본 모드: LiDAR 매칭·구독 / LIO: SLAM 활성화 |
| `use_viz` | `false` | RTAB-Map Viz 실행 |
| `odom_eval_mode` | `false` | 기본 모드 전용 / `true`: 외부 Odom TF 필요 |
| `use_sim_time` | `false` | LIO의 선언된 인자 / 기본 launch는 내부 참조만 |

## 입력·출력 토픽

| 데이터 | 대표 입력 | 동기화 출력 |
| --- | --- | --- |
| RGB | `/camera/color/image_raw` | `/my_go2/color/image_raw_sync` |
| Depth | `/camera/depth/image_rect_raw` | `/my_go2/depth/image_rect_raw_sync` |
| CameraInfo | `/camera/color/camera_info` | `/my_go2/color/camera_info_sync` |
| Odom | `/utlidar/robot_odom` | `/utlidar/robot_odom_sync` |
| LiDAR | `/utlidar/cloud_base` | `/utlidar/cloud_sync` |
| IMU | `/utlidar/imu` | `/utlidar/imu_sync` |

- 추가 입력 후보: [go2_topic_sync.py](./go2_topic_sync.py)
- RGB: raw·compressed 지원 / Depth: raw만 지원
- 디지털 트윈의 aligned depth 토픽: 현재 SLAM에서 미구독

## 동기화·좌표계

- RGB 기준 매칭: Depth 80ms · LiDAR 150ms · Odom 100ms
- Odom 매칭 실패: 버퍼의 최신값 사용
- RGB-D 입력 중단: 동기화 Odom 출력도 중단 가능
- IMU: 별도 재발행 · 묶음 매칭 제외

```text
map → odom → base_link → camera_link → camera_optical_frame
```

| 변환 | 발행 주체·설정 |
| --- | --- |
| `map → odom` | RTAB-Map |
| `odom → base_link` | 기본 동기화 노드 / 외부 LIO |
| `base_link → camera_link` | 고정 이동량 `(0.3, 0.0, 0.1) m` |

## 지도 저장

| 모드 | 저장 파일 |
| --- | --- |
| RGB-D / RGB-D + LiDAR | `maps/rtabmap_real.db` |
| 외부 LIO | `maps/rtabmap_real_lio.db` |

- 경로: `go2_real/slam/maps/` · Git 제외
- RGB-D 두 모드: 같은 DB 사용
- 실험별 보관: RTAB-Map 종료 후 DB 백업

## 상태 확인

별도 시스템 ROS 터미널:

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 node info /rtabmap
```

## 제약·관련 파일

| 범위 | 확인 사항 |
| --- | --- |
| 시간·프레임 | 센서 시계 차이·여러 입력 혼용·TF 중복 발행 |
| 카메라 | RGB/Depth 정합·고정 외부 파라미터·근사/재계산 내부 파라미터 |
| Depth | 영상 정합·깊이 단위 변환 없음 |
| 외부 LIO | 이 저장소에서 `*_synced` 생성 안 함 |
| 검증 | 지도 품질·동기화 정확도 추가 검증 필요 |

- 기본 실행: [go2_slam.launch.py](./go2_slam.launch.py)
- LIO 실행: [go2_slam_lio.launch.py](./go2_slam_lio.launch.py)
- RViz 설정: [go2_sim.rviz](./go2_sim.rviz)
- 이전 기록: [RTAB-Map 가이드](../../docs/archive/GO2_RTABMAP_GUIDE.md)

[메인](../../README.md) · [환경 설정](../../docs/setup.md) · [디지털 트윈](../digital_twin/README.md)
