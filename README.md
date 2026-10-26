# 自动晚签（simple-aimade-night-checkin-program）

用 AI 搓出来的低成本自动晚签工具：每天晚上到点自动完成宿舍晚签，成功/失败都会推一条微信通知到你手机上。
一开始我就猜到这个元小元使用什么vue和springboot做的, 那么就是说我可以直接试图用web request进行晚签到.
现在我有两个猜想:
1. 学校的学工系统还在建设中, 现在是测试阶段
2. 这东西也就这样了(感兴趣可以搜索天津岩石科技)
不过大软这么仁慈, 给点学分不过分吧.

> 前情提要：本仓库的教程操作较为简单，可以把这个当作学习的机会，况且真的会有收获🤓。作者是一个只会用 AI、讨厌晚签的普通人，不承诺后续更新和技术指导，出问题先看 `checkin.log`。
- 不会直接问ai
- 签到好麻烦，直接帮我签算了🤓

![都别说噢](./.assets/0073iSc7gy1iho7s345ipj30to0to76i.jpg "都别说噢")

## 能做什么

- 每晚在签到窗口（正常 20:00–22:44 / 晚归 22:45–23:59，以学校规则接口为准）内自动签到
- 工作日签"校内"，周六日签"校外"（日期自动判断）
- 每次用的照片都不一样：随机挑底图 + 细微改动（裁边/亮度/重压缩）+ 去 EXIF，改完即删，文件夹不膨胀
- 每天至少一条微信推送：自己签的报"签到成功"，提前签过的报"今日已签到"，token 失效/提交失败会告诉你去换 token
- token 死了自己续：有 `cred.txt` 的话走账号密码 + 验证码识别自动登录拿新 token
- 电脑端完全静默：无窗口运行（pythonw），失败才弹 Windows 通知，不抢游戏焦点

签到结果可在企业微信/学工页面里查到，日志在 `checkin.log`。

## 需要准备

1. 一台每晚 20:00–23:59 之间开着（睡眠也行，关机不行）且能联网的 Windows 电脑
2. 你的微信（收 Server酱 推送用）

不解释同学！！！不解释！！！

#### 实现需求及效果：

1.一台可以在晚上8点后联网的电脑
2.你的手机
3.一个注册server酱的账号（自己查是什么）

效果：每天的签到操作自动发到你的手机上

## 安装（项目独立虚拟环境）

需要 Python 3.10 或更高版本。Windows 用户安装 Python 时勾选 **Add to PATH**。

**Windows：**直接双击 `setup.bat`。

- 如果没有 `.venv` 就创建 → 装 `requirements.txt` → 当场验证 import，全绿才算完。慢的话按它的提示换清华镜像重装（不写 pip 配置）。

**Debian：**安装 Python 和 venv 支持，然后运行 `setup.sh`：

```bash
sudo apt update
sudo apt install python3 python3-venv
chmod +x setup.sh run.sh
./setup.sh
```

依赖装进本目录的 `.venv` 里，跟全局 Python 隔离（`requests` 调接口、`Pillow` 改图、`ddddocr` 验证码识别；Windows 另有 `windows-toasts` 本机通知）。

## 配置（4 样东西，全放本目录, 后续会使用）

| 文件 | 内容 | 怎么来 |
|---|---|---|
| 放几张宿舍照片（`*.jpg`） | 签到用的底图，至少 1 张 | 手机随手拍宿舍一角，然后放进来就行，EXIF 会被自动抹掉 |
| `token.txt`（这个不用管，后续程序自动续） | 登录凭证 `xgUniToken` | 登录学工页 → F12 → Application → Local Storage 里复制 |
| `cred.txt` | 第 1 行账号（学号），第 2 行密码，别加"账号："这类前缀 | 自己写，用于 token 过期时自动续期；不想要就删掉，过期时手动换 token |
| `sendkey.txt` | Server酱 SendKey | 微信扫码去 https://sct.ftqq.com 免费领一个（每天 5 条免费额度，只推关键消息完全够用） |

> ⚠️ `token.txt` / `cred.txt` / `sendkey.txt` 已在 `.gitignore` 里，**永远不要提交到 git**，也别发给别人。

## 使用（以下命令都用 `.venv` 的 python 跑）

- **手动跑一次**：Windows 双击 `run.bat`；Debian 在项目目录运行 `./run.sh`
- **只查不签**：Windows `.venv\Scripts\python.exe auto_checkin.py --dry-run`；Debian `./run.sh --dry-run`（零副作用，不推送）
- **测通知**：`--test-toast`（本机弹窗）、`--test-push`（微信推送）
- **测自动登录**：`--test-login`（会写回 `token.txt`）

Debian 桌面环境安装 `libnotify-bin` 后可用 `notify-send` 显示本机通知；没有桌面会话或该命令时会跳过本机通知，不影响 Server酱手机推送。可用 `./run.sh --test-toast` 测试本机通知，或用 `./run.sh --test-push` 测试推送。

## 自动签到

- windows的  **任务计划程序**  中 新建任务——程序填本目录 `.venv\Scripts\pythonw.exe`、参数填 `auto_checkin.py`、起始于填本目录；
- 触发器每天在你选择的时间执行一次, 勾上"随机延迟"，日志会自动记进 `checkin.log`。
- 改系统任务要管理员权限。
- Debian 可在 `crontab -e` 中添加定时任务，命令使用项目目录中 `run.sh` 的绝对路径，例如 `0 21 * * * /path/to/night-checkin/run.sh`。

退出码：`0`=成功，`1`=失败（看日志/等推送），`2`=不在窗口，`3`=已签，`4`=今晚无任务，`9`=崩溃，`99`=缺依赖。

排障先看 `checkin.log`：`推送通道未配置`说明 `sendkey.txt` 没放好；`FATAL` 行是崩溃堆栈。

## 文件说明

- `auto_checkin.py` —— 主流程：查规则 → 查状态 → 改图 → 上传 → 提交 → 复查 → 通知
- `auto_login.py` —— 自动续期：拿验证码 → OCR 识别 → 带签名登录（签名算法复刻自前端，纯本地计算）
- `run.bat` —— 双击手动跑签到（调用 `.venv`）
- `setup.bat` —— 双击建 `.venv` + 装依赖 + 验证
- `run.sh` / `setup.sh` —— Debian 手动运行及环境安装脚本
- `requirements.txt` —— 依赖
- `.venv/` —— 项目独立环境（几百 MB，不进 git，换电脑重建即可）

## 免责声明

仅供学习交流。请只用**自己的账号**、只读**自己的数据**。学校系统的操作都会留下日志，后果自负。
