---
description: Create or update a project task checklist in TASKS.md following a specific hierarchical format.
---

This workflow analyzes the current directory's work history and generates a structured checklist in `TASKS.md`.

### 1. Context Analysis
- Check the current directory for recent file modifications and git history (if available) to understand what tasks have been performed.
- Read the existing `TASKS.md` file if it exists to preserve current status.

### 2. File Generation / Update
- Create or update `TASKS.md` using the following Markdown structure:

```markdown
# [Project Name/Folder Name] 작업 현황

> [!NOTE]
> 이 파일은 작업의 진행 상황을 트래킹하기 위한 체크리스트입니다.

## 1. [Category Name] (English Category Name)

- [x] **[Main Task 1] ([English Task Name])**
  - [x] [Sub-task description]
  - [x] [Sub-task description]
  - [ ] [Pending sub-task]

- [ ] **[Main Task 2]**
  - [ ] [Sub-task description]
```

### 3. Rules
- **Language**: Use **Korean** for descriptions. English terms can be used in parentheses or for technical terms.
- **Checkboxes**:
  - `[x]`: Completed tasks.
  - `[ ]`: Pending or in-progress tasks.
- **Categorization**: Group tasks logically (e.g., Features, Bug Fixes, Refactoring, Documentation).
- ** Granularity**: Break down complex tasks into sub-tasks for better tracking.

### 4. Execution
- If `TASKS.md` already exists, append new tasks or update the status of existing ones based on recent activity.
- If it doesn't exist, create it based on the analysis of the current folder's content.
