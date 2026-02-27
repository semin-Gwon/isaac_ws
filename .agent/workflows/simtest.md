---
description: Antigravity-IsaacSim 통합 연동 및 시스템 안정성 검증을 위한 자동화 워크플로우
---

## 🛠 Rules
1. **환경 로드 우선순위**: 명령어 실행 전 반드시 `conda` 활성화와 `ROS2` 워크스페이스 소싱이 완료되어야 한다.
2. **DDS 설정 준수**: `CycloneDDS` 인터페이스(`eno1`)와 `ROS_DOMAIN_ID`가 환경 변수에 정확히 주입되었는지 검증한다.
3. **오류 처리 원칙 (중요)**: 실행 중 오류(Traceback, [Error], 물리 엔진 발산 등) 감지 시, **임의로 수정을 시도하거나 재시작하지 않는다.** 발생한 로그와 에러 메시지를 즉시 사용자에게 보고한 후 다음 지시를 기다린다.
4. **가변 인자 처리**: 실행 파일명은 외부에서 주입받는 변수(`{{TARGET_FILE}}`)로 처리한다.

---

## 🚀 Workflow Steps

### Step 1: Isaac Sim 및 ROS2 환경 구성
* **Command:**
    ```bash
    # 환경 활성화 및 워크스페이스 설정
    source ~/anaconda3/etc/profile.d/conda.sh && conda activate isaaclab
    source ~/isaac_ws/install/setup.bash
    
    # DDS 및 네트워크 환경 변수 설정
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export ROS_DOMAIN_ID=0
    export CYCLONEDDS_URI='<CycloneDDS><Domain><General><NetworkInterfaceAddress>eno1</NetworkInterfaceAddress></General></Domain></CycloneDDS>'
    
    # 작업 디렉토리 이동
    cd ~/isaac_ws
    ```
* **Terminal Validation:**
    * `echo $CONDA_DEFAULT_ENV` 결과가 `isaaclab`인지 확인
    * `ros2 topic list`를 실행하여 기본 토픽(`/rosout`, `/parameter_events`) 조회 여부 확인
* **Isaac Sim Console Validation:**
    * N/A (초기 설정 단계)

### Step 2: 대상 Python 파일 실행 및 실시간 검증
* **Command:**
    ```bash
    # 주입된 파일명으로 시뮬레이션 실행
    python3 {{TARGET_FILE}}
    ```
* **Terminal Validation:**
    * 터미널 출력 내 `Traceback`, `ModuleNotFoundError`, `ImportError` 키워드 존재 여부 확인
    * Process Exit Code가 `0`이 아닌 경우 실패로 처리
* **Isaac Sim Console Validation:**
    * `[carb.python.error]`: 스크립트 런타임 에러 집중 모니터링
    * `[omni.physx.plugin]`: RigidBody 또는 물리 엔진 연산 오류(NaN 등) 체크
    * `[omni.client.plugin]`: 에셋 로드 실패 및 경로 오류 체크

---