# FNTV Emby Compare

[简体中文](README.md) | **繁體中文** | [English](README.en.md)

---

🔍 **飛牛影視與 Emby 媒體識別對比工具。**

同一份媒體同時在飛牛影視和 Emby 建庫後，可能出現識別成不同作品、TMDb ID 缺失等問題。本工具按同名媒體庫及庫內相對路徑匹配電影和整部劇集，對比雙方的 TMDb ID，幫助定位可疑條目，供人工排查與修正。

媒體可以位於相同或不同網盤、掛載點；目錄不需要包含 `{tmdb-xxx}`。工具不需要訪問 TMDb API，也不需要 TMDb API Key。

**只讀檢查，不修改飛牛數據庫、不寫入 Emby、不自動修復。** 數據庫讀取會使用本地臨時快照，任務結束後清理；檢查結果只輸出到運行日誌。

## ✨ 功能

- 📚 按同名媒體庫配對；單邊存在的庫會列出並跳過，同名重複庫無法唯一匹配時停止檢查。
- 🗂️ 按實際路徑或庫內相對路徑覈對電影、整部劇集，支持不同掛載前綴及部分 STRM 媒體源場景。
- ⚠️ 輸出同路徑條目的 TMDb ID 不一致或缺失情況，保留雙方條目 ID 和路徑，方便手動定位。
- 📋 分別統計電影、整部劇集的共同及單邊獨有 TMDb ID；原始 ID 差集與排除同路徑差異後的獨有 ID 分開說明。
- 🔎 路徑無法唯一匹配、僅目錄一致但文件未覈實的條目單獨提示，不強行認定爲同一份媒體。
- 🛡️ 以只讀方式訪問飛牛數據庫，使用臨時快照及完整性檢查，兼容青龍容器的只讀目錄掛載。
- 🌐 支持 Emby HTTP/HTTPS，保留 HTTPS 證書驗證，API Key 通過請求頭發送，不放在請求 URL 中。
- 📝 正常媒體庫單行輸出；異常媒體庫先顯示摘要再展開詳情，最終彙總省略爲 0 的異常項。

## 📁 文件

```text
fntv-emby-compare/
├── fntv_emby_compare.py  # 主腳本
├── requirements.txt     # Python 依賴
├── LICENSE              # GNU GPL v3.0 許可證
├── README.md               # 簡體中文（預設）
├── README.zh-TW.md         # 繁體中文
└── README.en.md            # English
```

## 🚀 使用

### 1. 安裝依賴

建議使用 Python 3.10 或更高版本，以安裝當前版本的 `requests`：

```bash
python3 -m pip install -r requirements.txt
```

青龍面板可通過「依賴管理 → Python3」安裝 `requests`。其他依賴均爲 Python 標準庫。

### 2. 將飛牛數據庫目錄只讀掛載到青龍

在現有青龍服務的 `volumes` 下添加以下掛載，保留原有配置：

```yaml
services:
  qinglong:
    volumes:
      - /usr/local/apps/@appdata/trim.media/database:/ql/trim.media.database:ro
```

`qinglong` 應替換爲你實際的服務名稱；添加掛載後按原部署方式重新創建容器。宿主機路徑按實際飛牛安裝位置調整。

- 宿主機目錄：`/usr/local/apps/@appdata/trim.media/database`
- 容器內數據庫：`/ql/trim.media.database/trimmedia.db`
- `:ro`：禁止容器修改源數據庫目錄。

請掛載整個數據庫目錄，不要只掛載 `trimmedia.db`，以便讀取 `-wal`、`-shm` 等配套文件。容器運行用戶還需要有讀取權限，容器的臨時目錄需要可寫並有足夠空間。

腳本優先通過 SQLite 備份取得快照；只讀掛載無法直接打開時，會嘗試複製數據庫及存在的 WAL/journal 文件到臨時目錄，檢查複製前後的文件狀態並執行 `quick_check`。無法取得穩定快照或校驗失敗時停止檢查，不修改源數據庫。

### 3. 配置環境變量

在青龍「環境變量」中添加：

| 變量 | 示例 | 說明 |
| --- | --- | --- |
| `EMBY_URL` | `https://emby.example.com:8443` | 必填，Emby 服務地址，支持 HTTP/HTTPS |
| `EMBY_API_KEY` | `填寫自己的 Emby API Key` | 必填，Emby 管理後臺創建的 API Key |
| `FNTV_DB` | `/ql/trim.media.database/trimmedia.db` | 可選，飛牛數據庫在容器內的路徑，默認值即此路徑 |

本機終端運行時，也可以設置：

```bash
export EMBY_URL='https://emby.example.com:8443'
export EMBY_API_KEY='填寫自己的 Emby API Key'
export FNTV_DB='/ql/trim.media.database/trimmedia.db'
```

建議跨公網使用 HTTPS；HTTP 連接會以明文傳輸請求頭中的 Key。腳本不會自動使用系統代理，也不會自動跟隨重定向，請填寫最終可直接訪問的 Emby 地址。

### 4. 運行

上傳 `fntv_emby_compare.py`，青龍任務命令：

```bash
task fntv_emby_compare.py
```

本機運行：

```bash
python3 -u fntv_emby_compare.py
```

建議避開兩邊正在大量掃描或刮削的時間執行，減少數據在讀取期間發生變化的影響。

## 📊 日誌示例

以下爲完全虛構的演示數據，影片名稱、TMDb ID、條目 ID、路徑和數量均不代表實際媒體庫。此處爲繁體中文示例，腳本實際日誌仍使用簡體中文：

```text
同名媒體庫（2 個）：
劇集、電影
跳過 飛牛 獨有庫： 無
跳過 Emby 獨有庫： 無

========== 開始對比 ==========
✅ 劇集：共同 TMDB ID 5（整部劇集），無 ID 集合差異
⚠️ 電影：同路徑 ID 差異 1 處，排除同路徑差異後 Emby 獨有 ID 1 個
電影（按 TMDB ID 去重）：飛牛 10，Emby 11；共同 TMDB ID 9
  原始 ID 差集（含單獨列出的路徑差異及目錄候選）：飛牛 1；Emby 2
Emby電影獨有 TMDB ID（路徑差異及目錄候選另列）：
  示例電影丙 | TMDB: 900000003 | 條目: emby-example-02
    路徑： 演示分類/示例電影丙/示例電影丙.mkv
文件/劇集目錄路徑一致，但 TMDB ID 不同或缺失： 1
匹配方式： 庫內相對路徑一致
Emby:
  示例電影甲 | TMDB: 900000001 | 條目: emby-example-01
飛牛:
  示例電影乙 | TMDB: 900000002 | 條目: fntv-example-01
  共同路徑： 演示分類/示例電影甲/示例電影甲.mkv

========== 最終彙總 ==========
已對比同名媒體庫： 2 個（以下數量按庫累計）
電影（按 TMDB ID 去重）：飛牛 10 ；Emby 11 ；共同 TMDB ID 9
  原始 ID 差集（含路徑差異及目錄候選，飛牛/Emby）： 1/2
整部劇集（按 TMDB ID 去重）：飛牛 5 ；Emby 5 ；共同 TMDB ID 5
⚠️ 文件/劇集目錄路徑一致，但 TMDB ID 不同或缺失： 1
📋 排除同路徑 ID 差異條目後的獨有 ID（飛牛/Emby）： 0/1
ID 差異不代表已確認識別錯誤，請人工覈查。
單邊 ID 可能是未入庫或掃描範圍不同；未能對應的條目尚未覈實。
```

## 🔎 如何理解結果

- **同路徑 ID 差異**：對應路徑上的作品 ID 不同，或者至少一邊沒有 ID；需要人工確認哪邊識別有誤。
- **原始 ID 差集**：某個 ID 只出現在其中一邊的同名庫，可能包含已經單獨報告的路徑差異，因此不一定等於下面列出的獨有條目數量。
- **排除同路徑差異後的獨有 ID**：排除已找到對應路徑的差異項後剩餘的 ID；可能是未入庫、掃描範圍不同或識別問題。未覈實的目錄候選不作爲確認匹配排除。
- **目錄候選**：目錄相同但尚未覈實實際文件，不等同於已確認同一份媒體。
- **未關聯媒體庫**：飛牛數據庫中的電影/劇集條目沒有關聯到媒體庫，未參與本次比較；有此類條目時會在彙總提醒。

數量按每個媒體庫內的 TMDb ID 去重，最終彙總按庫累計，並非跨所有媒體庫全局去重。同一作品的多個版本可能對應一個 TMDb ID。

**兩邊 ID 集合一致不等於每個文件都識別正確；兩邊識別成同一個錯誤 ID，也可能沒有差異。** 本工具不把目錄中的 `{tmdb-xxx}` 當作正確答案，最終判斷仍由用戶完成。

## ⚠️ 使用範圍與限制

- 只比較電影和整部劇集，不比較季或單集 ID，也不校驗視頻內容、文件大小及劇集完整性。
- 主要面向兩邊具有相同媒體庫名稱、可對應目錄結構的場景；不同網盤不影響使用，但不同的分類層級或重命名可能導致路徑無法匹配。
- STRM 場景依賴 Emby 返回的路徑和媒體源信息，無法恢復實際路徑時會提示未匹配或目錄候選。
- 目錄不用帶 TMDb ID；當前版本不額外核對目錄標註與雙方識別結果。
- 飛牛數據庫結構或 Emby API 發生變化時可能需要適配；缺少必需字段或分頁異常時停止檢查，避免輸出不完整結果。
- 運行失敗時退出碼爲 `1`；發現識別差異但檢查正常完成時退出碼爲 `0`，差異通過日誌報告。

## 🛠️ 常見問題

**提示數據庫不存在**：檢查宿主機目錄、容器掛載及 `FNTV_DB`，注意腳本讀取的是容器內路徑。

**提示“只讀掛載無法直接打開”**：腳本正在嘗試臨時快照，不一定是檢查失敗；若後續仍報錯，檢查讀取權限、臨時空間和數據庫是否持續寫入。不要爲此改成可寫掛載。

**提示 HTTPS 證書錯誤**：檢查域名、證書鏈、證書有效期和容器時間，或爲受信任的自簽名證書配置可信 CA；不建議關閉驗證。

**有獨有 ID，但沒有逐條列出相同數量的條目**：查看同路徑差異及目錄候選部分，相關條目已經單獨列出，避免重複打印；ID 數量和多版本條目數量也可能不同。

**沒有匹配到同名庫**：檢查兩邊媒體庫名稱是否一致。課程、合集等單邊存在的庫會跳過，不會自動創建或修改。

## 📈 Star History

<a href="https://www.star-history.com/?repos=ffinly%2Ffntv-emby-compare&amp;type=date&amp;legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;theme=dark&amp;legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ffinly/fntv-emby-compare&amp;type=date&amp;legend=top-left" />
  </picture>
</a>

## 📄 License

本項目原創代碼採用 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）許可。
