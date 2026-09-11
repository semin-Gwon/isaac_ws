# Go2 RTAB-Map LiDAR Sync Implementation Plan

## Goal

Stabilize RTAB-Map SLAM when using RGB-D + LiDAR together.

Current status:

- RGB-D only (`use_lidar:=false`) works.
- RTAB-Map publishes valid `/cloud_map` and `/map` with RGB-D only.
- When LiDAR is enabled, RTAB-Map subscribes to `/utlidar/cloud_sync`, but often logs:
  - `Did not receive data since 5 seconds!`
- Root cause is not "missing topics", but unstable multi-topic synchronization.

The objective is to make `go2_topic_sync.py` produce truly synchronized bundles for RTAB-Map instead of only republishing messages with similar timestamps.

## Current Architecture

### Current flow

- Raw inputs come from:
  - RGB image
  - Depth image
  - CameraInfo (currently synthesized)
  - Odometry
  - LiDAR PointCloud2
- `go2_topic_sync.py` republishes:
  - `/my_go2/color/image_raw_sync`
  - `/my_go2/depth/image_rect_raw_sync`
  - `/my_go2/color/camera_info_sync`
  - `/utlidar/robot_odom_sync`
  - `/utlidar/cloud_sync`
- `go2_slam.launch.py` feeds these sync topics into RTAB-Map.

### Important limitation of the current sync node

The current sync node does timestamp alignment, not bundle synchronization.

What it does now:

- Store latest odom timestamp.
- When RGB arrives, overwrite its stamp with latest odom stamp and publish.
- When depth arrives, overwrite its stamp with latest odom stamp and publish.
- When LiDAR cloud arrives, overwrite its stamp with latest odom stamp and publish.

What it does not do:

- Hold RGB, depth, camera info, and cloud in buffers.
- Match them by time.
- Publish only when a valid set exists.

This is why RGB-D works reasonably well, but RGB-D + cloud often fails in RTAB-Map's approx sync stage.

## Key Design Decision

Do not remove RTAB-Map `approx_sync` yet.

Reason:

- The current sync node is not a true synchronizer yet.
- Removing RTAB-Map approx sync now would likely make behavior worse.
- First make `go2_topic_sync.py` produce consistent bundles.
- After that, reevaluate whether RTAB-Map sync parameters can be tightened or simplified.

## Target Architecture

### New responsibility split

`go2_topic_sync.py` should:

- buffer recent sensor messages
- match RGB, depth, camera info, LiDAR cloud, and odom by time
- publish synchronized outputs as one bundle

`rtabmap` should:

- continue consuming the sync topics
- keep `approx_sync:=true` for now
- receive much more consistent inputs than before

## Implementation Scope

Implement a minimum viable bundle synchronizer inside `go2_topic_sync.py`.

Do not attempt a full architectural rewrite in the first pass.

## Proposed Strategy

### Phase 1: Buffer-based bundle synchronization

Add explicit message buffers in `go2_topic_sync.py` for:

- RGB
- depth
- LiDAR cloud
- odom

CameraInfo is currently synthesized from RGB resolution, so it does not need an independent subscription buffer.

### Synchronization trigger

Use RGB arrival as the primary trigger.

When an RGB frame arrives:

1. Find the closest depth frame in time.
2. Find the closest LiDAR cloud in time.
3. Find the closest odom message or use the latest odom stamp.
4. If all required inputs are within configured tolerance, publish the synchronized bundle.
5. If not, skip publication and wait for the next RGB frame.

This keeps behavior deterministic and avoids publishing partially matched bundles.

## Matching Rules

### Recommended initial tolerances

Use configurable thresholds with conservative defaults:

- RGB <-> Depth: 0.08 s
- RGB <-> LiDAR cloud: 0.15 s
- RGB <-> Odom: 0.10 s or latest available odom

These values should be implemented as constants first, then optionally promoted to ROS params later.

### Publication rule

Only publish a synchronized set when:

- RGB exists
- matching depth exists
- matching LiDAR cloud exists when LiDAR mode is active
- odom stamp is available

When LiDAR mode is inactive:

- RGB + depth + camera info + odom is enough

## Output Behavior

All synchronized outputs in one bundle should share the same timestamp.

Recommended timestamp source:

- use matched odom timestamp if available
- otherwise use RGB timestamp only as a fallback

Outputs that should share the exact same stamp in one bundle:

- `/my_go2/color/image_raw_sync`
- `/my_go2/depth/image_rect_raw_sync`
- `/my_go2/color/camera_info_sync`
- `/utlidar/cloud_sync` when enabled
- `/utlidar/robot_odom_sync`

## Required Code Changes

### File: `go2_real/go2_topic_sync.py`

#### 1. Add message buffers

Add bounded deques for:

- `rgb_buffer`
- `depth_buffer`
- `cloud_buffer`
- `odom_buffer`

Keep only recent messages, for example 30 to 100 entries depending on topic rate.

#### 2. Store original timestamps

Do not immediately republish from individual callbacks.

Instead:

- push incoming messages into the corresponding buffer
- keep original message time for matching
- clone and restamp only when a synchronized bundle is selected

#### 3. Add helper methods

Implement helper methods such as:

- `msg_time_sec(msg)`
- `find_closest(buffer, stamp, max_delta_sec)`
- `prune_old_buffers()`
- `publish_synced_bundle(rgb_msg, depth_msg, cloud_msg, odom_msg)`

#### 4. RGB callback becomes trigger

Refactor RGB callback so it:

- decodes/completes RGB message if needed
- stores it in buffer
- attempts to build a synchronized set
- publishes only on success

#### 5. Depth callback becomes buffer-only

Depth callback should:

- normalize encoding if needed
- store message in buffer
- not publish immediately

#### 6. LiDAR callback becomes buffer-only

LiDAR cloud callback should:

- store message in buffer
- not publish immediately

#### 7. Odom callback behavior

Odom callback should continue to maintain:

- odom republishing
- `odom -> base_link` TF publication in slam mode

But also:

- store odom messages or at least store recent odom stamps in a small buffer for matching

#### 8. CameraInfo generation

Keep the current synthesized camera info logic.

But publish camera info only together with a successful synchronized RGB-D bundle.

#### 9. Diagnostics

Add sync diagnostics counters such as:

- `rgb_buffered`
- `depth_buffered`
- `cloud_buffered`
- `sync_success_count`
- `sync_drop_count`
- `last_sync_age_sec`

Log reasons for sync drops with throttling:

- no depth match
- no cloud match
- no odom match
- timestamp delta too large

### File: `go2_real/go2_slam.launch.py`

#### 1. Keep current branch split

Retain:

- RGB-D only branch when `use_lidar:=false`
- RGB-D + LiDAR branch when `use_lidar:=true`

#### 2. Keep `approx_sync:=true` for now

Do not remove RTAB-Map approx sync in this implementation pass.

#### 3. Keep current queue tuning

Current LiDAR branch already has larger:

- `approx_sync_max_interval`
- `topic_queue_size`
- `sync_queue_size`

Keep those values unless testing shows a regression.

## Validation Plan

### Stage 1: RGB-D regression check

Run:

```bash
ros2 launch /home/jnu/isaac_ws/go2_real/go2_slam.launch.py use_lidar:=false use_viz:=true
```

Verify:

- `/cloud_map` is non-empty
- `/map` is non-empty and visible in RViz
- no regression from current working RGB-D state

### Stage 2: LiDAR sync node output check

Run:

```bash
ros2 launch /home/jnu/isaac_ws/go2_real/go2_slam.launch.py use_lidar:=true use_viz:=true
```

Verify:

- `/utlidar/cloud_sync` is being published
- sync diagnostics show successful bundle publications
- sync drop logging is informative and throttled

### Stage 3: RTAB-Map subscriber check

Run:

```bash
ros2 node info /rtabmap
```

Verify subscriber list contains:

- `/my_go2/color/image_raw_sync`
- `/my_go2/depth/image_rect_raw_sync`
- `/my_go2/color/camera_info_sync`
- `/utlidar/robot_odom_sync`
- `/utlidar/cloud_sync` when LiDAR is enabled

### Stage 4: RTAB-Map health check

Verify these no longer occur or occur much less often:

- `Did not receive data since 5 seconds!`

Also verify:

```bash
ros2 topic echo /cloud_map --once
ros2 topic echo /map --once
ros2 topic echo /info --once
```

Expected:

- `/cloud_map` non-empty
- `/map` contains valid occupancy content
- `/info` updates continuously

### Stage 5: RViz validation

In RViz, compare:

- `RTABMap Cloud Map`
- `OccupancyGrid`
- `LiDAR Cloud Sync`

Recommended RViz interpretation:

- `RTABMap Cloud Map` = RTAB-Map output
- `LiDAR Cloud Sync` = synchronized LiDAR input

If RViz reports queue-full warnings, reduce RViz display load before changing SLAM code.

## Non-Goals For This Pass

Do not do these in the first implementation:

- remove RTAB-Map approx sync
- redesign the full launch topology
- add a second SLAM stack
- deeply tune RTAB-Map mapping parameters beyond current grid settings
- solve all RViz rendering issues in the sync node

## Risks

### Risk 1: LiDAR frequency is too different from RGB-D

Mitigation:

- widen cloud matching tolerance slightly
- optionally downsample LiDAR cloud later

### Risk 2: Buffer growth or stale matches

Mitigation:

- use bounded deques
- prune aggressively by age

### Risk 3: Odom timestamp behavior conflicts with eval mode

Mitigation:

- preserve current `odom_eval_mode` behavior
- only change how bundle publication chooses a shared stamp

## Suggested Implementation Order

1. Add buffers and helper functions in `go2_topic_sync.py`
2. Convert RGB callback into bundle trigger
3. Convert depth/cloud callbacks into buffer-only callbacks
4. Keep odom TF logic intact while adding odom matching support
5. Add sync diagnostics
6. Validate RGB-D regression
7. Validate RGB-D + LiDAR synchronization
8. Tune tolerance constants only if needed

## Success Criteria

Implementation is successful when:

- RGB-D only mode still works
- LiDAR-enabled mode no longer stalls RTAB-Map input
- `/cloud_map` and `/map` are produced with LiDAR enabled
- RTAB-Map does not repeatedly log "Did not receive data since 5 seconds!"
- RViz can show both RTAB-Map output and LiDAR input without misleading failures
