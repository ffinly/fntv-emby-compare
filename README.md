# FNTV Emby Compare

**简体中文** | [繁體中文](README.zh-TW.md) | [English](README.en.md)

---

🔍 **飞牛影视与 Emby 媒体识别对比工具。**

同一份媒体同时在飞牛影视和 Emby 建库后，可能出现识别成不同作品、TMDb ID 缺失等问题。本工具按同名媒体库及库内相对路径匹配电影和整部剧集，对比双方的 TMDb ID，帮助定位可疑条目，供人工排查与修正。

媒体可以位于相同或不同网盘、挂载点；目录不需要包含 `{tmdb-xxx}`。工具不需要访问 TMDb API，也不需要 TMDb API Key。

**只读检查，不修改飞牛数据库、不写入 Emby、不自动修复。** 数据库读取会使用本地临时快照，任务结束后清理；检查结果只输出到运行日志。

## ✨ 功能

- 📚 按同名媒体库配对；单边存在的库会列出并跳过，同名重复库无法唯一匹配时停止检查。
- 🗂️ 按实际路径或库内相对路径核对电影、整部剧集，支持不同挂载前缀及部分 STRM 媒体源场景。
- ⚠️ 输出同路径条目的 TMDb ID 不一致或缺失情况，保留双方条目 ID 和路径，方便手动定位。
- 📋 分别统计电影、整部剧集的共同及单边独有 TMDb ID；原始 ID 差集与排除同路径差异后的独有 ID 分开说明。
- 🔎 路径无法唯一匹配、仅目录一致但文件未核实的条目单独提示，不强行认定为同一份媒体。
- 🛡️ 以只读方式访问飞牛数据库，使用临时快照及完整性检查，兼容青龙容器的只读目录挂载。
- 🌐 支持 Emby HTTP/HTTPS，保留 HTTPS 证书验证，API Key 通过请求头发送，不放在请求 URL 中。
- 📝 正常媒体库单行输出；异常媒体库先显示摘要再展开详情，最终汇总省略为 0 的异常项。

## 📁 文件

```text
fntv-emby-compare/
├── fntv_emby_compare.py  # 主脚本
├── requirements.txt     # Python 依赖
├── LICENSE              # GNU GPL v3.0 许可证
├── README.md               # 简体中文（默认）
├── README.zh-TW.md         # 繁體中文
└── README.en.md            # English
```

## 🚀 使用

### 1. 安装依赖

建议使用 Python 3.10 或更高版本，以安装当前版本的 `requests`：

```bash
python3 -m pip install -r requirements.txt
```

青龙面板可通过「依赖管理 → Python3」安装 `requests`。其他依赖均为 Python 标准库。

### 2. 将飞牛数据库目录只读挂载到青龙

在现有青龙服务的 `volumes` 下添加以下挂载，保留原有配置：

```yaml
services:
  qinglong:
    volumes:
      - /usr/local/apps/@appdata/trim.media/database:/ql/trim.media.database:ro
```

`qinglong` 应替换为你实际的服务名称；添加挂载后按原部署方式重新创建容器。宿主机路径按实际飞牛安装位置调整。

- 宿主机目录：`/usr/local/apps/@appdata/trim.media/database`
- 容器内数据库：`/ql/trim.media.database/trimmedia.db`
- `:ro`：禁止容器修改源数据库目录。

请挂载整个数据库目录，不要只挂载 `trimmedia.db`，以便读取 `-wal`、`-shm` 等配套文件。容器运行用户还需要有读取权限，容器的临时目录需要可写并有足够空间。

脚本优先通过 SQLite 备份取得快照；只读挂载无法直接打开时，会尝试复制数据库及存在的 WAL/journal 文件到临时目录，检查复制前后的文件状态并执行 `quick_check`。无法取得稳定快照或校验失败时停止检查，不修改源数据库。

### 3. 配置环境变量

在青龙「环境变量」中添加：

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `EMBY_URL` | `https://emby.example.com:8443` | 必填，Emby 服务地址，支持 HTTP/HTTPS |
| `EMBY_API_KEY` | `填写自己的 Emby API Key` | 必填，Emby 管理后台创建的 API Key |
| `FNTV_DB` | `/ql/trim.media.database/trimmedia.db` | 可选，飞牛数据库在容器内的路径，默认值即此路径 |

本机终端运行时，也可以设置：

```bash
export EMBY_URL='https://emby.example.com:8443'
export EMBY_API_KEY='填写自己的 Emby API Key'
export FNTV_DB='/ql/trim.media.database/trimmedia.db'
```

建议跨公网使用 HTTPS；HTTP 连接会以明文传输请求头中的 Key。脚本不会自动使用系统代理，也不会自动跟随重定向，请填写最终可直接访问的 Emby 地址。

### 4. 运行

上传 `fntv_emby_compare.py`，青龙任务命令：

```bash
task fntv_emby_compare.py
```

本机运行：

```bash
python3 -u fntv_emby_compare.py
```

建议避开两边正在大量扫描或刮削的时间执行，减少数据在读取期间发生变化的影响。

## 📊 日志示例

以下为完全虚构的演示数据，影片名称、TMDb ID、条目 ID、路径和数量均不代表实际媒体库：

```text
同名媒体库（2 个）：
剧集、电影
跳过 飞牛 独有库： 无
跳过 Emby 独有库： 无

========== 开始对比 ==========
✅ 剧集：共同 TMDB ID 5（整部剧集），无 ID 集合差异
⚠️ 电影：同路径 ID 差异 1 处，排除同路径差异后 Emby 独有 ID 1 个
电影（按 TMDB ID 去重）：飞牛 10，Emby 11；共同 TMDB ID 9
  原始 ID 差集（含单独列出的路径差异及目录候选）：飞牛 1；Emby 2
Emby电影独有 TMDB ID（路径差异及目录候选另列）：
  示例电影丙 | TMDB: 900000003 | 条目: emby-example-02
    路径： 演示分类/示例电影丙/示例电影丙.mkv
文件/剧集目录路径一致，但 TMDB ID 不同或缺失： 1
匹配方式： 库内相对路径一致
Emby:
  示例电影甲 | TMDB: 900000001 | 条目: emby-example-01
飞牛:
  示例电影乙 | TMDB: 900000002 | 条目: fntv-example-01
  共同路径： 演示分类/示例电影甲/示例电影甲.mkv

========== 最终汇总 ==========
已对比同名媒体库： 2 个（以下数量按库累计）
电影（按 TMDB ID 去重）：飞牛 10 ；Emby 11 ；共同 TMDB ID 9
  原始 ID 差集（含路径差异及目录候选，飞牛/Emby）： 1/2
整部剧集（按 TMDB ID 去重）：飞牛 5 ；Emby 5 ；共同 TMDB ID 5
⚠️ 文件/剧集目录路径一致，但 TMDB ID 不同或缺失： 1
📋 排除同路径 ID 差异条目后的独有 ID（飞牛/Emby）： 0/1
ID 差异不代表已确认识别错误，请人工核查。
单边 ID 可能是未入库或扫描范围不同；未能对应的条目尚未核实。
```

## 🔎 如何理解结果

- **同路径 ID 差异**：对应路径上的作品 ID 不同，或者至少一边没有 ID；需要人工确认哪边识别有误。
- **原始 ID 差集**：某个 ID 只出现在其中一边的同名库，可能包含已经单独报告的路径差异，因此不一定等于下面列出的独有条目数量。
- **排除同路径差异后的独有 ID**：排除已找到对应路径的差异项后剩余的 ID；可能是未入库、扫描范围不同或识别问题。未核实的目录候选不作为确认匹配排除。
- **目录候选**：目录相同但尚未核实实际文件，不等同于已确认同一份媒体。
- **未关联媒体库**：飞牛数据库中的电影/剧集条目没有关联到媒体库，未参与本次比较；有此类条目时会在汇总提醒。

数量按每个媒体库内的 TMDb ID 去重，最终汇总按库累计，并非跨所有媒体库全局去重。同一作品的多个版本可能对应一个 TMDb ID。

**两边 ID 集合一致不等于每个文件都识别正确；两边识别成同一个错误 ID，也可能没有差异。** 本工具不把目录中的 `{tmdb-xxx}` 当作正确答案，最终判断仍由用户完成。

## ⚠️ 使用范围与限制

- 只比较电影和整部剧集，不比较季或单集 ID，也不校验视频内容、文件大小及剧集完整性。
- 主要面向两边具有相同媒体库名称、可对应目录结构的场景；不同网盘不影响使用，但不同的分类层级或重命名可能导致路径无法匹配。
- STRM 场景依赖 Emby 返回的路径和媒体源信息，无法恢复实际路径时会提示未匹配或目录候选。
- 目录不用带 TMDb ID；当前版本不额外核对目录标注与双方识别结果。
- 飞牛数据库结构或 Emby API 发生变化时可能需要适配；缺少必需字段或分页异常时停止检查，避免输出不完整结果。
- 运行失败时退出码为 `1`；发现识别差异但检查正常完成时退出码为 `0`，差异通过日志报告。

## 🛠️ 常见问题

**提示数据库不存在**：检查宿主机目录、容器挂载及 `FNTV_DB`，注意脚本读取的是容器内路径。

**提示“只读挂载无法直接打开”**：脚本正在尝试临时快照，不一定是检查失败；若后续仍报错，检查读取权限、临时空间和数据库是否持续写入。不要为此改成可写挂载。

**提示 HTTPS 证书错误**：检查域名、证书链、证书有效期和容器时间，或为受信任的自签名证书配置可信 CA；不建议关闭验证。

**有独有 ID，但没有逐条列出相同数量的条目**：查看同路径差异及目录候选部分，相关条目已经单独列出，避免重复打印；ID 数量和多版本条目数量也可能不同。

**没有匹配到同名库**：检查两边媒体库名称是否一致。课程、合集等单边存在的库会跳过，不会自动创建或修改。

## 📈 Star History

<a href="https://www.star-history.com/?repos=ffinly%2Ffntv-emby-compare&amp;type=date&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;theme=dark&amp;legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
  </picture>
</a>

## 📄 License

本项目原创代码采用 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）许可。
