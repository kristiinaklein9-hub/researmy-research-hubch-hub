# 快速入門

這份文件只處理第一次設定。目標是先讓你看到 research-hub 可以運作，再逐步接上 Zotero、Obsidian、NotebookLM 和 AI host。

## 1. 先選一條路徑

| 目標 | 指令 |
|---|---|
| 不連帳號，只看 dashboard | `pip install research-hub-pipeline` 後跑 `research-hub dashboard --sample` |
| 建立 demo vault | `pip install research-hub-pipeline` 後跑 `research-hub init --sample` |
| 匯入本機 PDF/DOCX/Markdown | `pip install "research-hub-pipeline[import,secrets]"` 後跑 `research-hub setup --persona analyst` |
| Zotero + Obsidian，不先跑 NotebookLM | `pip install "research-hub-pipeline[secrets]"` 後跑 `research-hub setup --skip-login` |
| Zotero + Obsidian + NotebookLM 完整流程 | `pip install "research-hub-pipeline[playwright,secrets]"` 後跑 `research-hub setup` |

## 2. 跑健康檢查

```bash
research-hub doctor
```

`doctor` 會檢查 config、vault 路徑、Zotero credentials、NotebookLM session，以及本機工作流程是否可用。

## 3. 第一次真實匯入

先不要把 NotebookLM 放進第一輪，讓問題範圍保持小一點：

```bash
research-hub auto "agent-based modeling" --max-papers 3 --no-nlm
```

如果你還沒有 LLM CLI，可先加上 `--no-fit-check`：

```bash
research-hub auto "agent-based modeling" --max-papers 3 --no-nlm --no-fit-check
```

這會跳過關聯性判斷，但仍保留 identifier 與 integrity 檢查。

## 4. 接上 NotebookLM

等本機流程成功後，再處理 NotebookLM 登入：

```bash
research-hub notebooklm login --auto-detect
```

Google OAuth 仍需要你在可見瀏覽器完成登入或手機驗證。research-hub 會在 NotebookLM 首頁載入後儲存 session。

接著跑：

```bash
research-hub notebooklm bundle --cluster <slug>
research-hub notebooklm upload --cluster <slug>
research-hub notebooklm generate --cluster <slug> --type brief
research-hub notebooklm download --cluster <slug>
```

## 5. 開 dashboard

```bash
research-hub serve --dashboard
```

瀏覽器打開 `http://127.0.0.1:8765/`，你可以檢查 cluster、paper、brief、diagnostics 和管理動作。

## 後續步驟

- 讀 [AI integrations](ai-integrations.md) 了解 MCP/REST/skills 如何給 Claude、Codex、Cursor、Gemini、OpenClaw 等 host 使用。
- 讀 [CLI reference](cli-reference.md) 查詢每個指令。
- 讀英文 [setup guide](../setup.md) 查看完整 installer 與 smoke test。
