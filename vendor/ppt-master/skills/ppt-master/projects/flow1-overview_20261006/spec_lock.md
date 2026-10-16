<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: 同学，老师
- objective: 让听众在 20 分钟内看懂这套流程在解决本科生的哪三个真实痛点、整条链路怎么走，并相信每一步的结果都可核对
- core_message: 把本科生「不知道从哪下手」的一句话，变成可联系导师、可引用、可溯源的科研入门材料——每一步都留下证据，取不到就如实说

## mode
- mode: custom
- mode_behavior: 结论先行、逐段举证——每页先落一句判断，再用机制图或对照表给证据，页尾一行过渡；阶段页统一「这一步解决什么 → 怎么做 → 交付什么」三段式。

## visual_style
- visual_style: blueprint
- visual_style_behavior: 细单一线宽线框承载全部结构，组件画成轮廓几何，几乎无圆角与填充；装饰即注记——尺寸线、引线箭头、组件代号、坐标标签，底下铺极淡网格；图纸编号栏固定一角，让线稿充当版式骨架。

## colors
- background: #FFFFFF
- secondary_bg: #F3F6F8
- primary: #0F4C5C
- accent: #F09A00
- secondary_accent: #3E8E7E
- body_text: #1B2A32
- secondary_text: #5A6B73
- divider: #D6DEE3
- positive: #2F7D4F
- negative: #B33A2B
- image_rendering: custom
- image_rendering_behavior: 扁平矢量主体配极细引导线与页边注记，少量半调纹理区分层级；只使用本套青绿与琥珀角色，无线宽变化、无投影、无渐变。

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- title_family: Microsoft YaHei, Arial, sans-serif
- body_family: Microsoft YaHei, Arial, sans-serif
- code_family: Consolas, monospace
- body: 32
- title: 56
- subtitle: 44
- annotation: 24
- footnote: 20
- code: 22
- card_title: 36

## icons
- library: tabler-outline
- stroke_width: 2
- inventory: tabler-outline/search, tabler-outline/map-pin, tabler-outline/list-check, tabler-outline/route, tabler-outline/file-text, tabler-outline/database, tabler-outline/circle-check, tabler-outline/alert-triangle, tabler-outline/users, tabler-outline/world, tabler-outline/lock, tabler-outline/clipboard-text, tabler-outline/chart-dots, tabler-outline/language, tabler-outline/eye, tabler-outline/network, tabler-outline/clock, tabler-outline/zoom-check

## page_rhythm
- P01: anchor
- P02: dense
- P03: dense
- P04: anchor
- P05: dense
- P06: dense
- P07: dense
- P08: dense
- P09: anchor
- P10: dense
- P11: dense
- P12: dense
- P13: dense
- P14: dense
- P15: breathing
- P16: dense
- P17: dense
- P18: dense
- P19: breathing

## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
- 本套内容没有需要「看起来像真实事物」的外部主体，流程、阶段、限制都由原生 SVG 图形承担最清楚——因此不排 AI 生成图与网图 (user)
