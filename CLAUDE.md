# BPM Converter — 專案說明

## 專案結構

```
bpm_converter/
├── app.py                  # Flask 主程式（BPM 轉換 + 播放器後端）
├── bpm_converter.py        # 命令列版本（獨立使用）
├── requirements.txt        # Python 依賴套件
├── docs/
│   └── index.html          # GitHub Pages 靜態跑步播放器
├── templates/
│   ├── index.html          # BPM 轉換頁面
│   ├── player.html         # 區網音樂播放器（需 Flask）
│   └── runner.html         # 跑步播放器（Flask 版）
└── downloads/              # 轉換後的 MP3（不進 git）
```

## 啟動本機伺服器

```powershell
cd "G:\我的雲端硬碟\43\000-公司資料\00-CODE\bpm_converter"
python -X utf8 app.py
```

瀏覽器開啟 http://localhost:5000

## GitHub 資訊

- **Repo**：https://github.com/micliou877/bpm-runner
- **GitHub Pages（跑步播放器）**：https://micliou877.github.io/bpm-runner/
- **帳號**：micliou877
- **Email**：micliou877@gmail.com

---

## 推送到 GitHub 的標準流程

### 前置確認

Git 憑證已儲存在 Windows Credential Manager，**不需要手動輸入密碼**。
可用以下指令確認憑證存在：

```bash
printf "protocol=https\nhost=github.com\n" | git credential fill
# 應顯示 username=micliou877 和 password=gho_...
```

### 每次推送步驟

```bash
cd "G:/我的雲端硬碟/43/000-公司資料/00-CODE/bpm_converter"

# 1. 確認變更內容
git status
git diff

# 2. 加入變更
git add .
# 或只加特定檔案：git add app.py templates/runner.html

# 3. Commit
git commit -m "說明這次改了什麼"

# 4. 推上 GitHub
git push
```

### 如果 GitHub Pages 的跑步播放器有更新

`docs/index.html` 是跑步播放器的靜態版本，內容與 `templates/runner.html` 相同。
**每次修改 runner.html 後，需同步更新 docs/index.html：**

```bash
cp "G:/我的雲端硬碟/43/000-公司資料/00-CODE/bpm_converter/templates/runner.html" \
   "G:/我的雲端硬碟/43/000-公司資料/00-CODE/bpm_converter/docs/index.html"
```

然後再執行上面的推送步驟。

### 如果憑證失效（重新授權）

憑證失效時 `git push` 會報 403 錯誤。處理方式：

```powershell
# 方法一：用 gh CLI 重新登入（在自己的終端機執行）
gh auth login
# 選 GitHub.com → HTTPS → Login with a web browser

# 方法二：直接提供 Personal Access Token
# 到 https://github.com/settings/tokens/new 產生新 token（勾選 repo）
# 然後：
echo "TOKEN內容" | gh auth login --with-token
```

### 建立全新 Repo（未來新專案）

```bash
TOKEN=$(printf "protocol=https\nhost=github.com\n" | git credential fill | grep password | cut -d= -f2)

# 建立 repo
curl -s -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d '{"name":"新repo名稱","description":"說明","private":false}'

# 設定 remote 並推送
git remote add origin https://github.com/micliou877/新repo名稱.git
git branch -M main
git push -u origin main

# 開啟 GitHub Pages（如需要，source 指向 docs/ 資料夾）
curl -s -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/micliou877/新repo名稱/pages \
  -d '{"source":{"branch":"main","path":"/docs"}}'
```
