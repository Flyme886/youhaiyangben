# Field Mapping

Default workbook shape is a social-media monitoring export.

## Required Columns

- `标题/微博内容`: copied verbatim after `原文：`
- `原文/评论链接`: copied as a raw URL line and used as the deduplication key
- `原文作者`: fallback author

## Optional Columns

- `转发/评论作者`: preferred author when not blank and not `无`; output is prefixed with `@`
- `序号`: displayed as `原序号` in the item metadata; falls back to Excel row number when missing
- `来源网站`: displayed in the item metadata when present
- `摘要`: may be used as a short `【概括】` source and violation-reason evidence
- `行业一级`, `行业二级`: used only as evidence hints for the evaluation note
- `信息属性`, `涉及词`: `涉及词` may be used as evidence for the evaluation note

## Violation Evaluation Policy

Generate a final Word evaluation box from auditable text:

- Excel text: `标题/微博内容`, `摘要`, `涉及词`, `行业一级`, `行业二级`
- Screenshot/page text: `screenshot_text` produced by `quality_check_screenshots.py`

Default categories follow the harmful-sample toolkit:

- `编造虚假信息`
- `歪曲事实`
- `煽动对立·借机引流`
- `攻击高校与制度（言语激烈、上纲上线）`

These four are the primary categories. Use secondary tags only to sharpen the one-sentence note or audit judgment:

- `人身攻击与侮辱谩骂`: insults, humiliation, personal abuse, or malicious derogatory labeling.
- `侵犯隐私与人肉搜索`: disclosure or solicitation of private identity, family, address, account, school-record, or relationship information.
- `网暴动员与群体围攻`: calls to attack, report, comment-flood, pressure, or collectively target people or institutions.
- `传播未经证实的阴谋论`: unsupported claims of black-box operations, power trading, protection networks, backstage relationships, or hidden chains.
- `恶意关联与扩大化攻击`: expanding a case to unrelated regions, groups, classes, families, schools, majors, or identities.
- `标题党与误导性引流`: sensational hooks, misleading titles, traffic bait, or topic stacking that overstates the evidence.
- `旧闻翻炒与重复炒作`: repeated amplification after a clarification, handling result, or prior public spread.
- `断章取义与选择性叙事`: selectively presenting fragments while omitting official clarification or key context.
- `情绪化审判与极端处置诉求`: disproportionate demands such as expulsion, imprisonment, lifetime bans, or punishment of relatives.
- `不相干蹭热点`: weakly related or unrelated material using the incident's keywords or tags for attention.

If a secondary tag is used in Word, keep it in the note, for example: `补充：断章取义与选择性叙事；仅截取处分结果，忽略官方说明。`

For video links, do not claim what appears in the video unless text was extracted from the page or screenshot. Add a manual-verification note for video image/audio.

Do not output `举报理由草稿` in final Word files. The visible final reason is the `【违规要素】... — ...` evaluation box.

## Missing Values

Treat these as blank:

- empty cells
- `无`
- `None`
- whitespace-only values

Do not guess missing titles, authors, or links.
