# 夜间射电观测排程台

值班员在页面上编辑或导入初始时刻与姿态、两轴转速以及 2–16 个观测目标，后端以**全局精确求解**（非贪心）给出最优观测序列，并展示每个目标的等待、转向、观测时段以及未选目标。

## 业务规则

- 时间均为当天整数秒（0–86400）；方位角 0–359°、俯仰角 0–90°，均为整数。
- 每个目标：唯一编号、方位角、俯仰角、持续秒数、可见窗 `[window_start, window_end]`、正整数优先级、必观标记。
- 方位轴按**圆周最短距离**转动，俯仰轴按**绝对差**转动，两轴可同时运行。
- 转向耗时 = `max(ceil(方位距离/方位转速), ceil(俯仰距离/俯仰转速))`。
- 到达过早可等待；观测 `[start, start+duration]` 必须完整落在可见窗内（在窗口内结束）。
- 优化目标按字典序依次进行：
  1. **必观目标全部入选**（硬约束，无可行序列时接口返回 `status: "infeasible"`）；
  2. 最大化总优先级；
  3. 最大化入选目标数；
  4. 最小化结束时刻；
  5. 以观测顺序的编号序列字典序决胜（编号按字符串比较）。

### 电缆包络模式（可选）

连续跟踪时电缆缠绕使方位轴存在软限位：同一 0–359° 物理方位可对应相差整圈（360°）的多个机械位置，沿用圆周最短转向可能越过软限位，或把后续必观目标逼到不可达。请求中给出可选的 `cable_envelope`：

- `reference_azimuth`：与 `initial_azimuth` **同余**（相差 360 的整数倍）的**展开方位**，即电缆零位对应的机械位置；
- `lower_limit` / `upper_limit`：整数软限位区间（含端点，跨度 ≤ 1440°），机械位置用展开方位（可超出 0–359）表示；
- 目标仍用 0–359° 方位表示；每次到达目标时，后端选择区间内与目标方位同余的展开位置（**圈位**），转向按展开坐标的有符号直线移动进行，起终点都在凸区间内故途中不会越界。

圈位选择与目标顺序**联合全局求解**（不是逐目标贪心取最近圈位），决胜规则在原有五级之后追加一级：

6. 同一编号序列再取**展开方位序列字典序最小**。

启用后每条结果额外给出 `azimuth_start` / `azimuth_end`（起止展开方位）与 `direction`（`cw` 展开方位增大 / `ccw` 减小 / `none`），配合 `slew.azimuth_seconds` 即顺逆方向与转角。若必观目标不存在共同可行的顺序与圈位组合（或某必观目标在区间内根本没有同余位置），返回 `status: "infeasible"` 与明确原因。未提供 `cable_envelope`（或显式 `null`）时，旧请求与结果语义完全不变。

## 求解算法

目标数 ≤ 16，采用子集动态规划全局求解：

1. 把"目标 × 圈内可选展开位置"笛卡尔展开为姿态状态（旧模式每目标恰一个位置）；
2. 前向 DP：`f[mask][s]` = 观测集合恰为 `mask` 且停在状态 `s` 的最早结束时刻；
3. 按 `(-总优先级, -目标数, 结束时刻)` 选出最优子集（可并列多个）；
4. 反向 DP 计算"最晚开始时刻表"，逐位贪心重建字典序最小的编号序列；电缆模式下同一步在可行圈位中取展开方位最小者，并列子集间再按展开方位序列决胜。

16 目标最坏情况约 1–2 秒（旧模式）/ 数秒（最松包络、每目标至多 5 个圈位）。正确性由测试中与暴力枚举（全排列 × 全圈位）对拍保证。

## 快速开始（Docker）

```bash
# 构建并启动合并部署的全栈应用（前端 + 后端同一容器）
docker compose up --build app

# 打开 http://localhost:8000 （宿主机端口可用 APP_PORT 覆盖）
APP_PORT=9000 docker compose up --build app
```

### 一次性校验服务 verify

```bash
docker compose run --rm verify
```

依次执行：后端 pytest 代码测试 → 前端构建检查（`tsc --noEmit && vite build`）→ 启动服务做 API 冒烟（健康检查、正常排程、可定位校验错误、必观不可行、电缆包络联合求解与不可行、前端托管），全部通过输出 `VERIFY PASSED` 并以退出码 0 结束，任一失败以非零退出。

### 可配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_PORT` | `8000` | 宿主机映射端口（容器内固定 8000） |
| `HEALTHCHECK_INTERVAL` | `30s` | 健康检查间隔 |
| `HEALTHCHECK_TIMEOUT` | `5s` | 健康检查超时 |
| `HEALTHCHECK_RETRIES` | `3` | 健康检查重试次数 |
| `HEALTHCHECK_START_PERIOD` | `10s` | 健康检查启动宽限 |

## API

### `GET /api/health`

```json
{"status": "ok", "service": "radio-scheduler"}
```

### `POST /api/schedule`

请求：

```json
{
  "initial_time": 72000,
  "initial_azimuth": 0,
  "initial_elevation": 45,
  "azimuth_speed": 2,
  "elevation_speed": 1,
  "cable_envelope": null,
  "targets": [
    {"id": "T1", "azimuth": 60, "elevation": 60, "duration": 600,
     "window_start": 72000, "window_end": 78000, "priority": 10, "must_observe": true}
  ]
}
```

启用电缆包络时把 `cable_envelope` 改为：

```json
"cable_envelope": {
  "reference_azimuth": 0,
  "lower_limit": -180,
  "upper_limit": 360
}
```

响应（`200 OK`）：

```json
{
  "status": "ok",
  "message": null,
  "observations": [
    {"target_id": "T1",
     "slew": {"azimuth_seconds": 30, "elevation_seconds": 15, "total_seconds": 30},
     "arrival_time": 72030, "wait_seconds": 0, "start": 72030, "end": 72630,
     "azimuth_start": null, "azimuth_end": null, "direction": null}
  ],
  "unscheduled": ["T2"],
  "total_priority": 10,
  "target_count": 1,
  "end_time": 72630,
  "cable_envelope": null
}
```

- 电缆包络模式下 `cable_envelope` 回显实际生效的区间；每条 observation 给出非空的 `azimuth_start`/`azimuth_end`/`direction`。
- `status: "infeasible"`：必观目标无法全部纳入任何可行序列（电缆模式下含"无共同可行的顺序与圈位组合"），仍为 `200`，`message` 说明原因。
- 字段非法：返回 `422`，`detail[].loc` 定位到具体字段（如 `["body", "targets", 0, "azimuth"]`、`["body", "cable_envelope", "upper_limit"]`），重复编号、窗口倒置、零位不同余等跨字段错误同样带定位信息。

页面端任何输入变化都会立即撤下旧结果，避免误读过期计划。

## 本地开发

```bash
# 后端（Python 3.11+）
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q          # 测试
.venv/bin/uvicorn app.main:app --reload      # 启动于 :8000

# 前端（Node 18+）
cd frontend
npm ci
npm run dev      # 开发服务器 :5173，/api 代理到 :8000
npm run build    # 类型检查 + 构建到 frontend/dist（后端自动托管）

# 冒烟（需服务已启动）
python scripts/smoke_test.py http://127.0.0.1:8000
```

## 项目结构

```
├── Dockerfile              # 多阶段：frontend-build / runtime / verify
├── docker-compose.yml      # app（合并部署）+ verify（一次性校验）
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 入口，托管 frontend/dist
│   │   ├── scheduler.py    # 全局精确求解器（子集 DP + 字典序重建）
│   │   └── schemas.py      # 请求/响应模型与可定位校验
│   └── tests/              # 求解器单测 + 暴力对拍 + API 测试
├── frontend/               # React + TypeScript + Vite
│   └── src/
│       ├── App.tsx         # 页面状态：输入变化即撤下旧结果
│       └── components/     # 条件表单、目标表格、结果视图、时间线
└── scripts/
    ├── verify.sh           # 测试 -> 构建检查 -> API 冒烟
    └── smoke_test.py       # 冒烟断言脚本（退出码报告）
```
