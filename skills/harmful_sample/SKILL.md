---
name: 有害样本.skill
description: Convert social-media monitoring Excel workbooks into deduplicated 50-item harmful-sample Word batches with original text, @author metadata, raw link, quality-checked screenshot, and evidence-based violation evaluation boxes. Use when the user submits `.xlsx` monitoring/export data and wants multiple `.docx` harmful-sample reports without invented content.
---

# 有害样本 Skill

Use this skill when a user provides an Excel monitoring/export workbook and wants harmful-sample Word reports split into 50 records per document. The workflow is deterministic: read fields from Excel, deduplicate by link, capture page screenshots, quality-check screenshots, use Excel/page/screenshot text as evidence when available, and generate `.docx` batches. Do not invent video or page content that was not extracted.

## Default Contract

- Input: one `.xlsx` workbook.
- Preferred sheet: `搜索结果`; otherwise use the first worksheet.
- Output directory: `输出Word/` unless the user specifies another path.
- Batch size: 50 unique links per Word file.
- Deduplication key: `原文/评论链接`; keep only the first row for each link and preserve first-seen order.
- Word record layout follows the harmful-sample toolkit style, in this exact order:
  1. `编号、（原序号 · 来源 · @作者）`
  2. `原文：` plus verbatim text from `标题/微博内容`
  3. `链接：` plus raw URL from `原文/评论链接`
  4. screenshot image only when quality is `usable` or `needs_review`; unusable screenshots become `截图待补：<reason>`
  5. orange evaluation box: `【违规要素】①... / ②... — 一句话判定理由`
- Do not print `举报理由草稿` in final Word output.
- Never add new factual content. If a value is missing, leave it blank or use the configured fallback only.

## Scripts

Use the bundled scripts instead of manually copying rows:

```bash
python skills/harmful_sample/scripts/excel_to_word.py input.xlsx --output-dir output --dry-run
python skills/harmful_sample/scripts/capture_links.py output/links_manifest.json --output-dir output/screenshots --engine playwright --profile desktop --full-page true
python skills/harmful_sample/scripts/quality_check_screenshots.py output/screenshots/screenshots_manifest.json
python skills/harmful_sample/scripts/excel_to_word.py input.xlsx --output-dir output --screenshots-manifest output/screenshots/screenshot_quality_manifest.json
```

For a quick smoke test, add `--limit 5 --batch-size 5`.

## Workflow

1. Inspect the workbook name and confirm it is the intended Excel input. Ignore Office lock files beginning with `~$`.
2. Run `excel_to_word.py --dry-run` to validate headers, count data rows, deduplicate links, and write `links_manifest.json`.
3. Run `capture_links.py` on `links_manifest.json`. It handles the bundled pnpm-style Playwright layout and, by default, installs the matching Chromium browser if it is missing. It records screenshot path, capture status, page title, and visible page text.
4. Run `quality_check_screenshots.py` on `screenshots_manifest.json`. This classifies screenshots as `usable`, `needs_review`, or `unusable`; detects black/white/low-information screenshots; and writes `screenshot_quality_manifest.json`.
5. Run `excel_to_word.py` again with the quality manifest to create harmful-sample Word batches and `处理日志.csv`.
6. Verify at least the first, a middle, and the last generated Word file structurally. Confirm each Word has at most 50 numbered items, numbering is continuous, author metadata uses `@`, no `举报理由草稿` remains, and unusable screenshots are represented as `截图待补`.

## Outputs

The output directory contains:

- `第01批.docx`, `第02批.docx`, ...: Word batches.
- `处理日志.csv`: audit log for included and duplicate rows.
- `links_manifest.json`: deduplicated link records used for screenshot capture.
- `screenshots/screenshots_manifest.json`: screenshot status for each unique record.
- `screenshots/*.png`: captured page screenshots when successful.
- `screenshots/screenshot_quality_manifest.json`: screenshot quality and extracted text evidence for Word generation.
- `screenshots/screenshot_quality.csv`: per-item screenshot quality audit.
- `截图状态汇总.csv`: counts by screenshot status.
- `问题记录.md`: issues found during the run.
- `优化方向.md`: concrete follow-up improvements.
- `完成标记.json`: machine-readable completion summary for polling or subagent verification.

## Field Mapping

Read `references/field_mapping.md` before changing column names or fallback behavior. The default mapping is intentionally narrow and should not guess alternate columns unless that file is updated.

## Violation Evaluation Rules

Default violation reasoning follows the bundled `舆情有害样本工具包` approach:

- ① `编造虚假信息`: unverified background or relationship rumors stated as fact, such as powerful family background, special access, internal appointment, or relation-based advancement.
- ② `歪曲事实`: distorting a dispute into embezzlement, criminal conduct, detention, sentencing, or other unsupported legal conclusions.
- ③ `煽动对立·借机引流`: using class/group opposition, inflammatory framing, curiosity-bait, topic-stacking, or traffic-seeking language.
- ④ `攻击高校与制度（言语激烈、上纲上线）`: escalating an individual incident into broad attacks on a university, education fairness, management, or institutions.

Keep these four as the primary categories in the Word evaluation box. When an item needs finer wording, use one of these secondary tags in the one-sentence note or audit log rather than creating many new primary categories:

- `人身攻击与侮辱谩骂`: insults, humiliation, or malicious personal attacks against involved students, staff, family members, schools, or teachers.
- `侵犯隐私与人肉搜索`: exposing, requesting, or guiding others to find names, family background, addresses, accounts, school records, or other private information.
- `网暴动员与群体围攻`: calling for users to attack, report, flood comments, pressure accounts, or collectively target a person or institution.
- `传播未经证实的阴谋论`: using unsupported black-box, power-trade, protection-network, or backstage-relationship narratives.
- `恶意关联与扩大化攻击`: expanding one incident into attacks on unrelated regions, groups, families, schools, majors, classes, or identities.
- `标题党与误导性引流`: using sensational titles, provocative hooks, misleading framing, or topic stacking when the evidence is thin.
- `旧闻翻炒与重复炒作`: repeatedly reposting, re-amplifying, or reframing an already handled or clarified incident to prolong negative attention.
- `断章取义与选择性叙事`: omitting official clarification, key context, timing, or facts while selecting only inflammatory fragments.
- `情绪化审判与极端处置诉求`: demanding expulsion, imprisonment, lifetime bans, punishment of relatives, or other disproportionate sanctions.
- `不相干蹭热点`: weakly related or unrelated content that borrows keywords, tags, or event heat for traffic.

Recommended visible style when using a secondary tag:

`【违规要素】③煽动对立·借机引流 / ④攻击高校与制度（言语激烈、上纲上线）　—　补充：网暴动员与群体围攻；号召网民围攻学校账号。`

The script generates the evaluation box directly from Excel fields and extracted page/screenshot text using conservative keyword rules. This is a final-document convenience, not a verified legal conclusion. For sensitive or ambiguous cases, review `处理日志.csv` and revise the generated note manually.

## Quality Rules

- Keep Excel text verbatim except trimming whitespace/control characters.
- Evaluation reasons must be evidence-based. Use only Excel fields (`标题/微博内容`, `摘要`, `涉及词`, `行业一级`, `行业二级`) and extracted page/screenshot text. For videos without transcript/visual OCR, explicitly say the video image/audio requires manual verification.
- Do not filter rows except by duplicate link.
- Do not stop the Word build because screenshots fail. Do not insert black/white/error/low-information screenshots into Word; use `截图待补：<reason>` instead.
- Preserve a compact Word style: A4 portrait, Songti/SimSun-style Chinese font, 10.5 pt, minimal decoration, readable screenshot width.
- When asked to use subagents, delegate independent verification only. Do not let subagents modify the same output files.
- The final answer should link only the deliverable Word files/log unless the user asks for scratch files.
